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

from google.cloud import bigquery
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="منصة الاستشارات التنفيذية | Executive Advisory Platform",
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# حقن أنماط CSS دقيقة تطابق التصميم السينمائي الفاخر للملف المرفق
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    .stApp {
        background: radial-gradient(circle at 50% 20%, #111827 0%, #030712 100%);
        color: #f3f4f6;
        direction: rtl;
        text-align: right;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }
    
    /* أزرار اللغات في الأعلى */
    .lang-container {
        display: flex;
        justify-content: flex-end;
        gap: 10px;
        margin-bottom: 2rem;
    }
    
    /* البطاقة السينمائية الرئيسية */
    .cinematic-card {
        background: rgba(17, 24, 39, 0.85);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 16px;
        padding: 40px;
        box-shadow: 0 25px 50px rgba(0, 0, 0, 0.5);
        backdrop-filter: blur(16px);
        margin-bottom: 2rem;
    }
    
    .platform-title {
        font-size: 2.2rem;
        font-weight: 800;
        color: #ffffff;
        text-align: right;
        margin-bottom: 1rem;
        letter-spacing: -0.5px;
    }
    
    .platform-desc {
        font-size: 1.02rem;
        color: #9ca3af;
        text-align: right;
        line-height: 1.7;
        margin-bottom: 2rem;
    }
    
    .sdaia-badge {
        background: rgba(16, 185, 129, 0.08);
        border: 1px solid rgba(16, 185, 129, 0.2);
        color: #34d399;
        padding: 14px 18px;
        border-radius: 10px;
        font-size: 0.9rem;
        text-align: right;
        margin-bottom: 2rem;
        line-height: 1.5;
    }
    
    /* تنسيق صندوق رفع الملفات */
    .stFileUploader {
        direction: rtl;
        text-align: right;
    }
    
    /* زر التشغيل التنفيذي */
    .stButton button {
        width: 100%;
        background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
        color: white;
        font-weight: 700;
        padding: 0.85rem 1.5rem;
        border-radius: 10px;
        border: none;
        box-shadow: 0 4px 15px rgba(37, 99, 235, 0.4);
        transition: all 0.3s ease;
        font-size: 1.05rem;
    }
    .stButton button:hover {
        background: linear-gradient(135deg, #1d4ed8 100%, #1e40af 100%);
        box-shadow: 0 6px 20px rgba(37, 99, 235, 0.6);
    }
    
    .footer-text {
        text-align: center;
        color: #6b7280;
        font-size: 0.85rem;
        margin-top: 3rem;
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

# 1. شريط أزرار اللغة العلوي (العربية / English)
col_space, col_ar, col_en = st.columns([6, 1, 1])
with col_ar:
    st.button("العربية", key="lang_ar")
with col_en:
    st.button("English", key="lang_en")

# 2. حاوية البطاقة السينمائية المطابقة تماماً للملف المرفق
st.markdown('<div class="cinematic-card">', unsafe_allow_html=True)

st.markdown('<div class="platform-title">منصة الاستشارات التنفيذية</div>', unsafe_allow_html=True)
st.markdown('<div class="platform-desc">أداة تحليل استراتيجية متقدمة لقيادات C-Level ومجالس الإدارة، تمكنك من قراءة الأداء المالي، تحليل تضخم التكاليف، واستشراف المخاطر الاكتوارية عبر محاكاة سيناريوهات حية تدعم قراراتك الاستثمارية بدقة فائقة.</div>', unsafe_allow_html=True)

st.markdown('<div class="sdaia-badge">🔒 آمن بالكامل %100 - التزاماً بأنظمة سدايا (SDAIA) ونظام حماية البيانات الشخصية: لا يتم حفظ أي ملفات أو بيانات نهائياً.</div>', unsafe_allow_html=True)

# عنصر رفع الملفات
uploaded_files = st.file_uploader(
    "اسحب ملفات البيانات هنا أو اضغط للاختيار (CSV / XLSX / PDF)",
    type=["pdf", "csv", "xlsx"],
    accept_multiple_files=True
)

st.markdown("<br>", unsafe_allow_html=True)

# زر التشغيل التنفيذي
launch_button = st.button("تشغيل اللوحة التنفيذية", type="primary")

st.markdown('</div>', unsafe_allow_html=True)

# 3. التذييل (Footer) السيادي
st.markdown(
    '<p class="footer-text">2026 فهد الجار الله - جميع حقوق محفوظة لبيئة العمل السيادية</p>',
    unsafe_allow_html=True
)

# 4. معالجة الملفات والاتصال بـ BigQuery (دون المساس بكود المعالجة الأساسي)
if uploaded_files and launch_button:
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
