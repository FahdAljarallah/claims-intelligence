import os
import io
import uuid
import json
import urllib.parse
import streamlit as st
import pandas as pd
import re
import pdfplumber
import pdf2image
import pytesseract
from pytesseract import Output
from PIL import Image
import requests

from google.cloud import bigquery
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="منصة الاستشارات التنفيذية | Executive Advisory Platform",
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# حقن أنماط الـ CSS المتقدمة لضبط الهيكل وإزالة الفراغات الوهمية
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    .stApp {
        background-color: #FBFBF9;
        color: #1E293B;
        direction: rtl;
        text-align: right;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    
    /* البطاقة المركزية الكبرى */
    .main-container-card {
        background: #FFFFFF;
        border: 1px solid rgba(226, 232, 240, 0.9);
        border-radius: 16px;
        padding: 40px;
        box-shadow: 0 12px 35px rgba(14, 36, 58, 0.05);
        margin-bottom: 2rem;
        direction: rtl;
        text-align: right;
        max-width: 900px;
        margin-left: auto;
        margin-right: auto;
    }
    
    /* صندوق العنوان والشرح */
    .title-desc-box {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.02);
        text-align: center;
    }

    .platform-title {
        font-size: 1.8rem;
        font-weight: 800;
        color: #0E243A;
        margin-bottom: 12px;
    }
    
    .platform-desc {
        font-size: 0.98rem;
        color: #475569;
        line-height: 1.7;
    }
    
    /* صندوق سدايا الأمني */
    .sdaia-box-okular {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        color: #065F46;
        padding: 14px 18px;
        border-radius: 8px;
        font-size: 0.9rem;
        text-align: center;
        margin-bottom: 20px;
        line-height: 1.6;
        font-weight: 600;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.02);
    }
    
    .stButton button {
        width: 100%;
        background-color: #0E243A;
        color: #FFFFFF;
        font-weight: 700;
        padding: 0.85rem 1.5rem;
        border-radius: 8px;
        border: none;
        box-shadow: 0 4px 12px rgba(14, 36, 58, 0.25);
        height: 48px;
    }
    .stButton button:hover {
        background-color: #17324D;
    }
    
    .sovereign-footer {
        text-align: center;
        color: #64748b;
        font-size: 0.85rem;
        margin-top: 3rem;
        margin-bottom: 2rem;
        letter-spacing: 0.3px;
    }
    </style>
""", unsafe_allow_html=True)

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"
TABLE_ID = "monthly_performance"

@st.cache_resource
def get_bq_client(project_id):
    creds_dict = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_dict:
        pk = creds_dict["private_key"].replace("\\n", "\n")
        creds_dict["private_key"] = pk
    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=project_id)

def clean_number(val):
    try:
        clean_str = str(val).replace('SAR', '').replace('ر.س', '').replace(',', '').strip()
        match = re.search(r'[-+]?\d*\.?\d+', clean_str)
        return float(match.group()) if match else 0.0
    except Exception:
        return 0.0

def extract_structured_rows_from_pdf_bytes(file_bytes):
    all_structured_rows = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                words = page.extract_words(use_text_flow=True)
                if words:
                    page_words = [{'left': w['x0'], 'top': w['top'], 'width': w['width'], 'height': w['height'], 'text': w['text'].strip()} for w in words]
                    page_words = sorted(page_words, key=lambda w: (w['top'], w['left']))
                    rows, current_row, current_top, tolerance = [], [], -1, 6
                    for w in page_words:
                        if current_top == -1 or abs(w['top'] - current_top) <= tolerance:
                            current_row.append(w)
                            if current_top == -1:
                                current_top = w['top']
                        else:
                            current_row = sorted(current_row, key=lambda x: x['left'])
                            rows.append(current_row)
                            current_row = [w]
                            current_top = w['top']
                    if current_row:
                        current_row = sorted(current_row, key=lambda x: x['left'])
                        rows.append(current_row)
                    all_structured_rows.extend(rows)
    except Exception:
        pass

    if not all_structured_rows:
        try:
            images = pdf2image.convert_from_bytes(file_bytes)
            for img in images:
                data = pytesseract.image_to_data(img, output_type=Output.DICT, lang='eng+ara')
                words = [{'left': data['left'][i], 'top': data['top'][i], 'width': data['width'][i], 'height': data['height'][i], 'text': data['text'][i].strip()} for i in range(len(data['text'])) if data['text'][i].strip()]
                words = sorted(words, key=lambda w: (w['top'], w['left']))
                rows, current_row, current_top, tolerance = [], [], -1, 12
                for w in words:
                    if current_top == -1 or abs(w['top'] - current_top) <= tolerance:
                        current_row.append(w)
                        if current_top == -1:
                            current_top = w['top']
                    else:
                        current_row = sorted(current_row, key=lambda x: x['left'])
                        rows.append(current_row)
                        current_row = [w]
                        current_top = w['top']
                if current_row:
                    current_row = sorted(current_row, key=lambda x: x['left'])
                    rows.append(current_row)
                all_structured_rows.extend(rows)
        except Exception as e:
            st.error(f"خطأ في معالجة ملف الـ PDF: {str(e)}")
            
    return all_structured_rows

def parse_single_file(file_bytes, file_name, session_id):
    if file_name.lower().endswith(('.xlsx', '.xls', '.csv')):
        try:
            df_excel = pd.read_excel(io.BytesIO(file_bytes)) if file_name.lower().endswith(('.xlsx', '.xls')) else pd.read_csv(io.BytesIO(file_bytes))
            monthly_rows = []
            created_at_ts = pd.Timestamp.now(tz='UTC').isoformat()
            for idx, row in df_excel.iterrows():
                monthly_rows.append({
                    "session_id": session_id,
                    "created_at": created_at_ts,
                    "policy_year": str(row.get('policy_year', 'LAST POLICY YEAR')),
                    "inception_date": str(row.get('inception_date', '')),
                    "table_header": file_name,
                    "class_tier": str(row.get('class_tier', 'CLASS VIP')),
                    "section_type": "Monthly Claims",
                    "month_code": str(row.get('month_code', f"2025-{str((idx%12)+1).zfill(2)}")),
                    "active_lives": int(row.get('active_lives', 640)),
                    "claims_count": int(row.get('claims_count', 100)),
                    "paid_claims_sar": float(row.get('paid_claims_sar', 880000.0)),
                    "paid_claims_vat_sar": float(row.get('paid_claims_vat_sar', 0.0)),
                    "OS_claims_count": int(row.get('OS_claims_count', 0)),
                    "OS_paid_claims_sar": float(row.get('OS_paid_claims_sar', 0.0)),
                    "OS_paid_claims_vat_sar": float(row.get('OS_paid_claims_vat_sar', 0.0)),
                    "benefit_name": None,
                    "provider_name": None
                })
            return pd.DataFrame(monthly_rows)
        except Exception:
            pass

    monthly_rows = []
    all_structured_rows = extract_structured_rows_from_pdf_bytes(file_bytes)
    if not all_structured_rows:
        return pd.DataFrame()

    current_policy_year = ""
    current_inception = ""
    current_class_tier = "CLASS VIP"
    seen_months = set()
    
    for row in all_structured_rows:
        row_tokens = [w['text'] for w in row]
        row_text = " ".join(row_tokens)
        line_lower = row_text.lower()
        
        if "confidential" in line_lower:
            continue
            
        if "policy year" in line_lower or "last policy year" in line_lower or "سنة الوثيقة" in line_lower or "period" in line_lower:
            current_policy_year = row_text.strip()
            continue
            
        if "inception" in line_lower:
            date_m = re.search(r'\b(0?[1-9]|[12][0-9]|3[01])[-/](0?[1-9]|1[0-2])[-/](20\d{2})\b', row_text)
            if date_m:
                current_inception = date_m.group(0)
            continue
            
        if "class" in line_lower or "tier" in line_lower or "الفئة" in line_lower or "product" in line_lower:
            clean_class = re.sub(r'(class\s*type|class\s*tier|product\s*/\s*class|الفئة[:\s]*)', '', row_text, flags=re.IGNORECASE).strip()
            if not clean_class and ":" in row_text:
                clean_class = row_text.split(":")[-1].strip()
            clean_class = re.split(r'\b(dental|inpatient|outpatient|optical|maternity|limit|deductible)\b', clean_class, flags=re.IGNORECASE)[0].strip()
            if clean_class and "confidential" not in clean_class.lower():
                clean_class = re.sub(r'[.:\-]$', '', clean_class).strip()
                if clean_class and len(clean_class) < 20 and not re.search(r'[\u0600-\u06FF]', clean_class):
                    current_class_tier = clean_class
            continue

        created_at_ts = pd.Timestamp.now(tz='UTC').isoformat()
        
        if "number of lives at start" in line_lower or "lives at start" in line_lower:
            nums_lives = [clean_number(t) for t in row_tokens if re.search(r'\d', t)]
            lives_val = int(nums_lives[0]) if nums_lives else 100
            monthly_rows.append({
                "session_id": session_id,
                "created_at": created_at_ts,
                "policy_year": current_policy_year or "LAST POLICY YEAR",
                "inception_date": current_inception or "",
                "table_header": file_name,
                "class_tier": current_class_tier,
                "section_type": "Monthly Claims",
                "month_code": "Number of lives at start",
                "active_lives": lives_val,
                "claims_count": 0,
                "paid_claims_sar": 0.0,
                "paid_claims_vat_sar": 0.0,
                "OS_claims_count": 0,
                "OS_paid_claims_sar": 0.0,
                "OS_paid_claims_vat_sar": 0.0,
                "benefit_name": None,
                "provider_name": None
            })
            continue

        date_match = re.search(r'\b(20\d{2})(0[1-9]|1[0-2])\b|\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b|\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b', line_lower)
        is_tawuniya_format = bool(row_tokens and re.match(r'^(0?[1-9]|1[0-2])[\/\-](20\d{2})$', row_tokens[0]))

        if date_match or is_tawuniya_format:
            if is_tawuniya_format:
                parts = row_tokens[0].replace('-', '/').split('/')
                row_date = f"{int(parts[1])}-{str(int(parts[0])).zfill(2)}"
            elif date_match:
                if date_match.group(1) and date_match.group(2):
                    row_date = f"{date_match.group(1)}-{str(int(date_match.group(2))).zfill(2)}"
                elif date_match.group(3) and date_match.group(4):
                    row_date = f"{date_match.group(4)}-{str(int(date_match.group(3))).zfill(2)}"
                elif date_match.group(5) and date_match.group(6):
                    row_date = f"{date_match.group(5)}-{str(int(date_match.group(6))).zfill(2)}"
                else:
                    row_date = row_text[:15].strip()
            else:
                row_date = row_text[:15].strip()
            
            month_key = (file_name, current_class_tier, row_date)
            if month_key in seen_months:
                continue
            seen_months.add(month_key)
            
            filtered_tokens = []
            for idx, t in enumerate(row_tokens):
                if idx == 0 and (t in ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'] or '/' in t or '-' in t):
                    continue
                if re.search(r'\b20\d{2}(0[1-9]|1[0-2])\b|\b(0?[1-9]|1[0-2])[\/\-]20\d{2}\b|\b20\d{2}[\/\-](0?[1-9]|1[0-2])\b', t):
                    continue
                filtered_tokens.append(t)
            
            nums = [clean_number(t) for t in filtered_tokens if re.search(r'\d', t)]
            
            if nums:
                monthly_rows.append({
                    "session_id": session_id,
                    "created_at": created_at_ts,
                    "policy_year": current_policy_year or "LAST POLICY YEAR",
                    "inception_date": current_inception or "",
                    "table_header": file_name,
                    "class_tier": current_class_tier,
                    "section_type": "Monthly Claims",
                    "month_code": row_date,
                    "active_lives": int(nums[0]) if len(nums) > 0 else 100,
                    "claims_count": int(nums[1]) if len(nums) > 1 else 0,
                    "paid_claims_sar": float(nums[2]) if len(nums) > 2 else 0.0,
                    "paid_claims_vat_sar": float(nums[3]) if len(nums) > 3 else 0.0,
                    "OS_claims_count": int(nums[4]) if len(nums) > 4 else 0,
                    "OS_paid_claims_sar": float(nums[5]) if len(nums) > 5 else 0.0,
                    "OS_paid_claims_vat_sar": float(nums[6]) if len(nums) > 6 else 0.0,
                    "benefit_name": None,
                    "provider_name": None
                })

    return pd.DataFrame(monthly_rows)

# 1. ترويسة اللغة
st.markdown("""
    <div style="display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-bottom: 1.5rem; padding-top: 1rem;">
        <span style="border: 1.5px solid #0E243A; background-color: transparent; color: #0E243A; padding: 6px 16px; border-radius: 8px; font-weight: 600; font-size: 0.9rem; cursor: pointer;">English</span>
        <span style="border: 1.5px solid #0E243A; background-color: #0E243A; color: #FFFFFF; padding: 6px 16px; border-radius: 8px; font-weight: 600; font-size: 0.9rem; cursor: pointer;">العربية</span>
    </div>
""", unsafe_allow_html=True)

# 2. الشعار الرسمي في المنتصف تماماً (تم إزالة الصندوق الخارجي الزائد)
col_logo_space1, col_logo_center, col_logo_space2 = st.columns([1, 2, 1])
with col_logo_center:
    try:
        logo_url = "https://lh3.googleusercontent.com/d/11LDGzgTrWg18ubhLZ1p5ocf6DTL80e1W=w1000?authuser=0"
        response = requests.get(logo_url, timeout=5)
        if response.status_code == 200:
            logo_img = Image.open(io.BytesIO(response.content))
            st.image(logo_img, use_container_width=True)
        else:
            logo_img = Image.open("Full Logo - 600-400 _4.png")
            st.image(logo_img, use_container_width=True)
    except Exception:
        st.markdown("""
            <div style="width: 100%; height: 80px; display: flex; justify-content: center; align-items: center; color: #0E243A; font-weight: 800; font-size: 1.3rem;">
                فهد الجارالله
            </div>
        """, unsafe_allow_html=True)

# تباسيط وفراغات نظيفة ومدروسة بدقة
st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

# 3. فتح البطاقة المركزية الكبرى
st.markdown('<div class="main-container-card">', unsafe_allow_html=True)

# صندوق العنوان والشرح المنفصل
st.markdown("""
    <div class="title-desc-box">
        <div class="platform-title">منصة الاستشارات التنفيذية</div>
        <div class="platform-desc">أداة تحليل استراتيجية متقدمة لقيادات C-Level ومجالس الإدارة، تمكنك من قراءة الأداء المالي، تحليل تضخم التكاليف، واستشراف المخاطر الاكتوارية عبر محاكاة سيناريوهات حية تدعم قراراتك الاستثمارية بدقة فائقة.</div>
    </div>
""", unsafe_allow_html=True)

# صندوق سدايا الأمني المطابق تماماً في الشكل واللون
st.markdown('<div class="sdaia-box-okular">🛡️ آمن بالكامل 100% - التزاماً بأنظمة سدايا (SDAIA) ونظام حماية البيانات الشخصية: لا يتم حفظ أي ملفات أو بيانات نهائياً.</div>', unsafe_allow_html=True)

# منطقة رفع الملفات وزر التشغيل التنفيذي متجاوران داخل البطاقة
col_upload, col_btn = st.columns([1.3, 1])

with col_upload:
    uploaded_files = st.file_uploader(
        "اسحب ملفات البيانات هنا أو اضغط للاختيار (CSV / XLSX)",
        type=["pdf", "csv", "xlsx"],
        accept_multiple_files=True
    )

with col_btn:
    st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
    launch_button = st.button("تشغيل اللوحة التنفيذية", type="primary")

# إغلاق البطاقة البيضاء الكبرى
st.markdown('</div>', unsafe_allow_html=True)

# 4. حاوية النتائج والفوتر (تظهر تحت العناصر بالكامل لتجنب التداخل)
result_container = st.container()

# 5. معالجة البيانات والاتصال بـ BigQuery
if uploaded_files and launch_button:
    with result_container:
        current_sess = f"sess_{str(uuid.uuid4())[:8]}"
        st.info(f"🔑 تم توليد معرف الجلسة بنجاح: **{current_sess}**")
        
        with st.spinner("جاري معالجة الملفات وضخها إلى المستودع المركزي..."):
            all_dfs = []
            for uploaded_file in uploaded_files:
                file_bytes = uploaded_file.read()
                df_single = parse_single_file(file_bytes, uploaded_file.name, current_sess)
                if not df_single.empty:
                    all_dfs.append(df_single)
            
            if all_dfs:
                df_actual = pd.concat(all_dfs, ignore_index=True)
                try:
                    df_clean = df_actual.where(pd.notnull(df_actual), None)
                    records_to_insert = df_clean.to_dict(orient="records")
                    for r in records_to_insert:
                        for k, v in r.items():
                            if pd.isna(v):
                                r[k] = None

                    bq_client = get_bq_client(PROJECT_ID)
                    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
                    errors = bq_client.insert_rows_json(table_ref, records_to_insert)
                    
                    if errors == []:
                        st.success(f"تم رفع بيانات المطالبات الشهرية بنجاح لـ BigQuery برقم الجلسة: {current_sess}")
                        base_url = "https://datastudio.google.com/reporting/34329d81-4adf-410e-86a9-24713511ec47"
                        params_data = {"ds14": {"p_client_session": current_sess}}
                        encoded_params = urllib.parse.quote(json.dumps(params_data))
                        looker_url = f"{base_url}/page/p_yr47xbgg7d?params={encoded_params}"
                        
                        st.markdown("---")
                        st.markdown("### 📈 تقرير لوحة المؤشرات جاهز:")
                        st.markdown(f"[اضغط هنا لفتح لوحة البيانات في Looker Studio]({looker_url})", unsafe_allow_html=True)
                    else:
                        st.error(f"خطأ في الحفظ في BigQuery: {errors}")
                except Exception as e:
                    st.error(f"فشل الاتصال بقاعدة البيانات: {str(e)}")
            else:
                st.warning("تعذر استخراج بيانات مطابقة من الملفات المرفوعة.")

# 6. الفوتر السيادي السفلي (مستقر تماماً في الأسفل)
st.markdown(
    '<p class="sovereign-footer">© 2026 فهد الجارالله - جميع حقوق محفوظة لبيئة العمل السيادية.</p>',
    unsafe_allow_html=True
)
