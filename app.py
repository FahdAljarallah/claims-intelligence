import os
import io
import streamlit as st
import pandas as pd
import numpy as np
import re
import pdfplumber
import pdf2image
import pytesseract
from pytesseract import Output
from PIL import Image
import uuid
import urllib.parse

from google.cloud import bigquery
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="مرصد المطالبات التأمينية الذكي", page_icon="📊", layout="wide")

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"
TABLE_ID = "monthly_performance"
# تحديث رابط لوحة القيادة ليتوافق مع اسم البارامتر الموجود في Looker Studio (p_session_id)
LOOKER_BASE_URL = "https://lookerstudio.google.com/reporting/34329d81-4adf-410e-86a9-24713511ec47"

# في جزء توليد الرابط داخل الكود، تأكد من استخدام المفتاح الصحيح:
params = {"p_session_id": current_sess}
encoded_params = urllib.parse.urlencode(params)
final_dashboard_url = f"{LOOKER_BASE_URL}?{encoded_params}"

@st.cache_resource
def get_bq_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_dict:
        pk = creds_dict["private_key"].replace("\\n", "\n")
        creds_dict["private_key"] = pk
    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)

def clean_number(val):
    try:
        if pd.isna(val):
            return 0.0
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
                    page_words = [{
                        'left': w['x0'], 'top': w['top'], 'width': w['width'], 'height': w['height'], 'text': w['text'].strip()
                    } for w in words]
                    
                    page_words = sorted(page_words, key=lambda w: (w['top'], w['left']))
                    rows, current_row, current_top, tolerance = [], [], -1, 6
                    
                    for w in page_words:
                        if current_top == -1 or abs(w['top'] - current_top) <= tolerance:
                            current_row.append(w)
                            if current_top == -1: current_top = w['top']
                        else:
                            rows.append(sorted(current_row, key=lambda x: x['left']))
                            current_row, current_top = [w], w['top']
                    if current_row:
                        rows.append(sorted(current_row, key=lambda x: x['left']))
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
                        if current_top == -1: current_top = w['top']
                    else:
                        rows.append(sorted(current_row, key=lambda x: x['left']))
                        current_row, current_top = [w], w['top']
                if current_row:
                    rows.append(sorted(current_row, key=lambda x: x['left']))
                all_structured_rows.extend(rows)
        except Exception as e:
            st.error(f"خطأ في معالجة ملف الـ PDF: {str(e)}")
            
    return all_structured_rows

def parse_single_file(file_bytes, file_name, session_id):
    monthly_rows, benefit_rows, provider_rows = [], [], []
    all_structured_rows = extract_structured_rows_from_pdf_bytes(file_bytes)
    if not all_structured_rows: return pd.DataFrame()

    current_section, current_policy_year, current_class_tier = "monthly", "", "CLASS VIP"
    
    for row in all_structured_rows:
        row_tokens = [w['text'] for w in row]
        row_text = " ".join(row_tokens)
        line_lower = row_text.lower()
        
        if "confidential" in line_lower: continue
        if "policy year" in line_lower or "last policy year" in line_lower or "سنة الوثيقة" in line_lower:
            current_policy_year = row_text.strip()
            continue
            
        if "class" in line_lower or "tier" in line_lower or "الفئة" in line_lower:
            clean_class = re.sub(r'(class\s*type|class\s*tier|الفئة[:\s]*)', '', row_text, flags=re.IGNORECASE).strip()
            if clean_class and "confidential" not in clean_class.lower():
                current_class_tier = clean_class.rstrip('.')
            continue

        if ("breakdown" in line_lower and "benefit" in line_lower) or "التوزيع حسب المنفعة" in line_lower:
            current_section = "benefit"
            continue
        if ("top 20" in line_lower) or ("utilised" in line_lower and "provider" in line_lower) or "مزودى الخدمة" in line_lower:
            current_section = "providers"
            continue
        if any(w in line_lower for w in ["annual claims", "monthly claim", "lives at start"]) or re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b', line_lower):
            if not any(k in line_lower for k in ["top 20", "breakdown by benefit"]):
                current_section = "monthly"

        created_at_ts = pd.Timestamp.now(tz='UTC').isoformat()
        
        base_record = {
            "session_id": str(session_id),
            "created_at": created_at_ts,
            "policy_year": current_policy_year or "LAST POLICY YEAR",
            "table_header": file_name,
            "class_tier": current_class_tier,
            "section_type": current_section,
            "month_code": None,
            "active_lives": 0,
            "claims_count": 0,
            "paid_claims_sar": 0.0,
            "paid_claims_vat_sar": 0.0,
            "OS_claims_count": 0,
            "OS_paid_claims_sar": 0.0,
            "OS_paid_claims_vat_sar": 0.0,
            "benefit_name": None,
            "provider_name": None
        }

        if current_section == "monthly":
            if "lives at start" in line_lower:
                nums_lives = [clean_number(t) for t in row_tokens if re.search(r'\d', t)]
                rec = base_record.copy()
                rec.update({
                    "section_type": "Monthly Claims",
                    "month_code": "Number of lives at start", 
                    "active_lives": int(nums_lives[0]) if nums_lives else 50,
                })
                monthly_rows.append(rec)
                continue

            date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b|\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b', line_lower)
            if date_match:
                row_date = f"{date_match.group(2)}-{date_match.group(1).zfill(2)}" if date_match.group(1) else f"{date_match.group(3)}-{date_match.group(4).zfill(2)}"
                nums = [clean_number(t) for t in row_tokens if re.search(r'\d', t)]
                if nums:
                    rec = base_record.copy()
                    rec.update({
                        "section_type": "Monthly Claims",
                        "month_code": row_date, 
                        "active_lives": int(nums[0]) if len(nums) > 0 else 50,
                        "claims_count": int(nums[1]) if len(nums) > 1 else 0, 
                        "paid_claims_sar": float(nums[2]) if len(nums) > 2 else 0.0,
                        "paid_claims_vat_sar": float(nums[3]) if len(nums) > 3 else 0.0, 
                        "OS_claims_count": int(nums[4]) if len(nums) > 4 else 0,
                        "OS_paid_claims_sar": float(nums[5]) if len(nums) > 5 else 0.0, 
                        "OS_paid_claims_vat_sar": float(nums[6]) if len(nums) > 6 else 0.0,
                    })
                    monthly_rows.append(rec)
                    
        elif current_section == "benefit":
            all_nums = [clean_number(t) for t in row_tokens if re.search(r'\d', t)]
            if len(all_nums) < 2: continue
            benefit_labels = ["Out-Patient", "In-Patient", "Dental", "Optical", "Maternity", "Consultation", "Pharmacy"]
            b_name = next((b for b in benefit_labels if b.lower() in line_lower), "General Benefit")
            
            rec = base_record.copy()
            rec.update({
                "section_type": "Breakdown by Benefit",
                "month_code": "Benefit Summary", 
                "claims_count": int(all_nums[0]) if len(all_nums) > 0 else 0, 
                "paid_claims_sar": float(all_nums[1]) if len(all_nums) > 1 else 0.0,
                "paid_claims_vat_sar": float(all_nums[2]) if len(all_nums) > 2 else 0.0, 
                "OS_claims_count": int(all_nums[3]) if len(all_nums) > 3 else 0,
                "OS_paid_claims_sar": float(all_nums[4]) if len(all_nums) > 4 else 0.0, 
                "OS_paid_claims_vat_sar": float(all_nums[5]) if len(all_nums) > 5 else 0.0,
                "benefit_name": b_name
            })
            benefit_rows.append(rec)
            
        elif current_section == "providers":
            all_nums = [clean_number(t) for t in row_tokens if re.search(r'\d', t)]
            if len(all_nums) < 2: continue
            prov_name = row_text[:30].strip()
            
            rec = base_record.copy()
            rec.update({
                "section_type": "Top 20 Providers",
                "month_code": "Provider Summary", 
                "claims_count": int(all_nums[0]) if len(all_nums) > 0 else 0, 
                "paid_claims_sar": float(all_nums[1]) if len(all_nums) > 1 else 0.0,
                "paid_claims_vat_sar": float(all_nums[2]) if len(all_nums) > 2 else 0.0, 
                "OS_claims_count": int(all_nums[3]) if len(all_nums) > 3 else 0,
                "OS_paid_claims_sar": float(all_nums[4]) if len(all_nums) > 4 else 0.0, 
                "OS_paid_claims_vat_sar": float(all_nums[5]) if len(all_nums) > 5 else 0.0,
                "provider_name": prov_name
            })
            provider_rows.append(rec)

    df_res = pd.DataFrame(monthly_rows + benefit_rows + provider_rows)
    if not df_res.empty:
        # تطهير وتنقية القيم الفارغة والـ NaN وتحويلها إلى None لضمان سلامة JSON payload لـ BigQuery
        df_res = df_res.replace({np.nan: None, pd.NA: None})
        for col in df_res.columns:
            if df_res[col].dtype == object:
                df_res[col] = df_res[col].where(df_res[col].notnull(), None)
    return df_res

st.title("مرصد المطالبات التأمينية الذكي")
st.markdown("منصة تحليل محفظة التأمين الصحي المؤسسي — ارفع تقرير المطالبات الخاص بك لتوليد رمز الجلسة وعرض لوحة القرار فوراً.")

uploaded_files = st.file_uploader("رفع تقارير المطالبات المالية (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.button("معالجة المستندات وتوليد رمز الجلسة", type="primary"):
        with st.spinner("جاري قراءة الملفات، تطهير البيانات، وإرسالها إلى الجدول المركزي..."):
            session_id = f"sess_{uuid.uuid4().hex[:8]}"
            all_dfs = [parse_single_file(f.read(), f.name, session_id) for f in uploaded_files]
            valid_dfs = [df for df in all_dfs if not df.empty]
            
            if valid_dfs:
                combined_df = pd.concat(valid_dfs, ignore_index=True)
                combined_df = combined_df.replace({np.nan: None, pd.NA: None})
                
                try:
                    bq_client = get_bq_client()
                    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
                    records_to_insert = combined_df.to_dict(orient="records")
                    
                    errors = bq_client.insert_rows_json(table_ref, records_to_insert)
                    
                    if errors == []:
                        st.session_state["active_session_id"] = session_id
                        st.success(f"تمت المعالجة والضخ بنجاح دون أي أخطاء! رمز الجلسة الخاص بك هو: `{session_id}`")
                    else:
                        st.error(f"خطأ أثناء حفظ البيانات في المستودع: {errors}")
                except Exception as e:
                    st.error(f"فشل الاتصال بقاعدة البيانات: {str(e)}")
            else:
                st.warning("تعذر استخراج بيانات من الملفات المرفوعة، تأكد من صحة الملفات.")

    if "active_session_id" in st.session_state:
        current_sess = st.session_state["active_session_id"]
        
        st.markdown("---")
        st.info(f"🔑 **رمز الجلسة الفعّال:** `{current_sess}`")
        
        params = {f"df_session_id": current_sess}
        encoded_params = urllib.parse.urlencode(params)
        final_dashboard_url = f"{LOOKER_BASE_URL}?{encoded_params}"
        
        st.markdown(
            f"""
            <div style="text-align: center; padding: 20px;">
                <a href="{final_dashboard_url}" target="_blank">
                    <button style="background-color: #0083B8; color: white; font-size: 20px; padding: 12px 35px; border: none; border-radius: 8px; cursor: pointer; font-weight: bold;">
                        🚀 عرض لوحة القرار التنفيذي التفاعلية
                    </button>
                </a>
            </div>
            """,
            unsafe_allow_html=True
        )
