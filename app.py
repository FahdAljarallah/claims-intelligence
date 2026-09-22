import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import json
import urllib.parse
import time
import re
import io
import pypdf

st.set_page_config(page_title="Claims Intelligence Portal", page_icon="📊", layout="wide")

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"
LOOKER_REPORT_URL = "https://lookerstudio.google.com/reporting/34329d81-4adf-410e-86a9-24713511ec47/page/1f97F"

@st.cache_resource
def get_bq_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_dict:
        pk = creds_dict["private_key"].replace("\\n", "\n")
        if "-----BEGIN PRIVATE KEY-----" not in pk:
            clean_body = pk.replace("-----BEGIN PRIVATE KEY-----", "").replace("-----END PRIVATE KEY-----", "").strip()
            if clean_body.startswith("nMI"):
                clean_body = clean_body[1:]
            pk = f"-----BEGIN PRIVATE KEY-----\n{clean_body}\n-----END PRIVATE KEY-----"
        creds_dict["private_key"] = pk
    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)

def safe_clean_number(val):
    if pd.isna(val) or val is None:
        return 0.0
    val_str = str(val).replace('SAR', '').replace('ر.س', '').replace(',', '').strip()
    if val_str.count('.') > 1:
        parts = val_str.rsplit('.', 1)
        val_str = parts[0].replace('.', '') + '.' + parts[1]
    match = re.search(r'[-+]?\d*\.?\d+', val_str)
    return float(match.group()) if match else 0.0

def extract_file_inception_date(file_bytes):
    text = ""
    try:
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        for page in reader.pages[:2]:
            t = page.extract_text()
            if t:
                text += t + "\n"
        for line in text.split('\n'):
            l_low = line.lower()
            if any(kw in l_low for kw in ["inception", "effective", "period from", "from date", "processed to", "policy period"]):
                return line.strip()
    except Exception:
        pass
    return "Not Specified"

# محرك الاستخراج المحلي الصافي والمتوافق مع بنية ملفات الـ PDF
def parse_pdf_claims_local_robust(file_bytes, file_name, session_id, default_members):
    cleaned_records = []
    file_inception = extract_file_inception_date(file_bytes)
    
    extracted_text = ""
    try:
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        for page in reader.pages:
            t = page.extract_text()
            if t:
                extracted_text += t + "\n"
                
        lines = [l.strip() for l in extracted_text.split('\n') if l.strip()]
        current_tier = "GENERAL CLASS"
        current_policy_section = "Last Policy Year"
        
        i = 0
        while i < len(lines):
            line = lines[i]
            l_low = line.lower()
            
            if any(kw in l_low for kw in ["last policy year", "prior policy year", "policy year"]):
                if len(line) < 45:
                    current_policy_section = line
                    
            if "class" in l_low or "vip" in l_low or "category" in l_low:
                if len(line) < 50:
                    current_tier = line
            
            # عزل صف البداية ديناميكياً (Number of lives at start)
            if "lives at start" in l_low or "number of lives at start" in l_low:
                nums = re.findall(r'\b\d{1,3}(?:,\d{3})*\b', line)
                if nums:
                    start_val = safe_clean_number(nums[0])
                    cleaned_records.append({
                        'session_id': str(session_id),
                        'created_at': pd.Timestamp.now(tz='UTC'),
                        'policy_year': 'RAW_TEST',
                        'policy_year_label': 'Policy Start Reference',
                        'source_file': file_name,
                        'policy_inception_date': file_inception,
                        'month_code': 'START_LIVES',
                        'month_weight': 0.0,
                        'class_tier': current_tier,
                        'active_lives': float(start_val),
                        'claims_count': 0.0,
                        'paid_claims_sar': 0.0,
                        'paid_claims_vat_sar': 0.0,
                        'outstanding_claims_count': 0.0,
                        'outstanding_claims_sar': 0.0,
                        'outstanding_claims_vat_sar': 0.0
                    })

            # التقاط الأشهر والصفوف الشهرية واستخراج الأرقام ديناميكياً
            date_match = re.search(r'\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b|\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b', line)
            if date_match:
                if date_match.group(1) and date_match.group(2):
                    month_code = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}"
                else:
                    month_code = f"{date_match.group(4)}-{date_match.group(3).zfill(2)}"
                
                context_numbers = []
                for j in range(i, min(i + 12, len(lines))):
                    potential_nums = re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b', lines[j])
                    for num_str in potential_nums:
                        clean_val = safe_clean_number(num_str)
                        if clean_val >= 0:
                            context_numbers.append(clean_val)
                
                if len(context_numbers) >= 2:
                    lives = context_numbers[0] if context_numbers[0] > 0 else default_members
                    claims_cnt = context_numbers[1] if len(context_numbers) > 1 else 0.0
                    amt_before = context_numbers[2] if len(context_numbers) > 2 else 0.0
                    amt_after = context_numbers[3] if len(context_numbers) > 3 else amt_before
                    os_cnt = context_numbers[4] if len(context_numbers) > 4 else 0.0
                    os_before = context_numbers[5] if len(context_numbers) > 5 else 0.0
                    os_after = context_numbers[6] if len(context_numbers) > 6 else 0.0
                    
                    cleaned_records.append({
                        'session_id': str(session_id),
                        'created_at': pd.Timestamp.now(tz='UTC'),
                        'policy_year': 'RAW_TEST',
                        'policy_year_label': current_policy_section,
                        'source_file': file_name,
                        'policy_inception_date': file_inception,
                        'month_code': month_code,
                        'month_weight': 1.0,
                        'class_tier': current_tier,
                        'active_lives': float(lives),
                        'claims_count': float(claims_cnt),
                        'paid_claims_sar': float(amt_before),
                        'paid_claims_vat_sar': float(amt_after),
                        'outstanding_claims_count': float(os_cnt),
                        'outstanding_claims_sar': float(os_before),
                        'outstanding_claims_vat_sar': float(os_after)
                    })
            i += 1
    except Exception as e:
        st.error(f"خطأ في معالجة المستند {file_name}: {str(e)}")
        
    return cleaned_records

def process_preview_files(uploaded_files, session_id, default_members):
    all_m = []
    for f in uploaded_files:
        if f.name.lower().endswith('.pdf'):
            file_bytes = f.read()
            m_recs = parse_pdf_claims_local_robust(file_bytes, f.name, session_id, default_members)
            all_m.extend(m_recs)
    return pd.DataFrame(all_m), pd.DataFrame(), pd.DataFrame()

def upload_data_to_bigquery(df_monthly):
    client = get_bq_client()
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.monthly_performance"
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
        autodetect=True
    )
    job = client.load_table_from_dataframe(df_monthly, table_ref, job_config=job_config)
    job.result()

st.title("مرصد المطالبات | الاستخراج المحلي المعتمد")
st.markdown("استخراج آلي ومحلي بالكامل 100% لمعالجة تقارير المطالبات بدقة تامة ودون أي خدمات مدفوعة.")

col_date, col_members = st.columns(2)
with col_date:
    inception_date = st.date_input("تاريخ بداية سريان الوثيقة", value=None)
with col_members:
    total_members = st.number_input("إجمالي عدد المؤمن عليهم (Lives)", min_value=1, max_value=1000000, value=None, step=1)

col_prem, _ = st.columns(2)
with col_prem:
    current_premium = st.number_input("قسط الوثيقة السنوي الحالي (SAR)", min_value=1000.0, max_value=500000000.0, value=None, step=50000.0, format="%.2f")

uploaded_files = st.file_uploader("رفع ملفات تجربة المطالبات (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    
    if st.button("معالجة واستخراج محلي", type="secondary"):
        if not current_premium or not total_members or not inception_date:
            st.warning("يرجى تعبئة الحقول الأساسية.")
        else:
            with st.spinner("جاري قراءة المعطيات محلياً..."):
                st.session_state.pop("preview_m", None)
                df_m, _, _ = process_preview_files(uploaded_files, session_id, total_members)
                st.session_state["preview_m"] = df_m
                st.session_state["temp_session_id"] = session_id
                
                unique_files = df_m['source_file'].unique() if not df_m.empty else []
                total_records = len(df_m)
                st.success(f"تمت معالجة {len(unique_files)} ملفات بنجاح (`{', '.join(unique_files)}`) بإجمالي {total_records} سجلاً محلياً!")

    if "preview_m" in st.session_state and not st.session_state["preview_m"].empty:
        st.subheader("🔍 معاينة جدول الأداء المحلي (Local Robust Preview)")
        st.dataframe(st.session_state["preview_m"], use_container_width=True)
        
        csv_m = st.session_state["preview_m"].to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 تحميل جدول الأداء كاملًا (CSV)",
            data=csv_m,
            file_name="local_robust_performance.csv",
            mime="text/csv",
        )
        
        if st.button("اعتماد وضخ البيانات محلياً إلى BigQuery", type="primary"):
            with st.spinner("جاري الضخ إلى المستودع..."):
                upload_data_to_bigquery(st.session_state["preview_m"])
                st.success("تم ضخ البيانات بنجاح إلى BigQuery وجاهزة بالكامل للتحليل التنفيذي!")
