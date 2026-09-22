import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import json
import urllib.parse
import time
import re
import io
import pdfplumber
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

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

# محرك استخراج قوي ومستقر يضمن قراءة ملفات المزودين معاً (التعاونية وميدغلف)
def parse_pdf_claims_stable(file_bytes, file_name, session_id):
    cleaned_records = []
    file_inception = "30/11/2025" if "ce" in file_name.lower() else "01-12-2024"
    
    # قراءة حقيقية واستخراج كامل السجلات لملف ميدغلف والتعاونية
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            current_tier = "CLASS VIP"
            current_policy_section = "Last Policy Year"
            
            for page in pdf.pages:
                t = page.extract_text()
                if not t:
                    continue
                lines = [l.strip() for l in t.split('\n') if l.strip()]
                
                i = 0
                while i < len(lines):
                    line = lines[i]
                    l_low = line.lower()
                    
                    if any(kw in l_low for kw in ["last policy year", "prior policy year", "policy year"]):
                        if len(line) < 45:
                            current_policy_section = line
                            
                    if "class" in l_low or "vip" in l_low:
                        if len(line) < 50:
                            current_tier = line
                    
                    date_match = re.search(r'\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b|\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b', line)
                    if date_match:
                        if date_match.group(1) and date_match.group(2):
                            month_code = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}"
                        else:
                            month_code = f"{date_match.group(4)}-{date_match.group(3).zfill(2)}"
                        
                        context_numbers = []
                        for j in range(i, min(i + 10, len(lines))):
                            potential_nums = re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b', lines[j])
                            for num_str in potential_nums:
                                clean_val = safe_clean_number(num_str)
                                if clean_val >= 0:
                                    context_numbers.append(clean_val)
                        
                        if len(context_numbers) >= 2:
                            lives = context_numbers[0]
                            claims_cnt = context_numbers[1] if len(context_numbers) > 1 else 0.0
                            amt_before = context_numbers[2] if len(context_numbers) > 2 else 0.0
                            amt_after = context_numbers[3] if len(context_numbers) > 3 else amt_before
                            
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
                                'paid_claims_vat_sar': float(amt_after)
                            })
                    i += 1
    except Exception as e:
        st.error(f"خطأ في قراءة الملف {file_name}: {str(e)}")
        
    return cleaned_records

def process_preview_files(uploaded_files, session_id):
    all_m = []
    for f in uploaded_files:
        if f.name.lower().endswith('.pdf'):
            file_bytes = f.read()
            m_recs = parse_pdf_claims_stable(file_bytes, f.name, session_id)
            all_m.extend(m_recs)
    return pd.DataFrame(all_m)

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

st.title("مرصد المطالبات | محرك الاستخراج المستقر")
st.markdown("استخراج البيانات الخام الفعلية من تقارير المزودين بمعالجة دقيقة وآمنة.")

uploaded_files = st.file_uploader("رفع ملفات تجربة المطالبات (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    
    if st.button("استخراج ومعاينة البيانات الخام", type="secondary"):
        with st.spinner("جاري قراءة واستخراج الملفات..."):
            df_m = process_preview_files(uploaded_files, session_id)
            st.session_state["preview_m"] = df_m
            st.session_state["temp_session_id"] = session_id
            
            unique_files = df_m['source_file'].unique() if not df_m.empty else []
            total_records = len(df_m)
            st.success(f"تمت معالجة الملفات بنجاح (`{', '.join(unique_files)}`) بإجمالي {total_records} سجلاً مستخرجاً!")

    if "preview_m" in st.session_state and not st.session_state["preview_m"].empty:
        st.subheader("🔍 معاينة جدول البيانات الخام (Raw Extracted Data)")
        st.dataframe(st.session_state["preview_m"], use_container_width=True)
        
        csv_m = st.session_state["preview_m"].to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 تحميل جدول البيانات الخام (CSV)",
            data=csv_m,
            file_name="stable_raw_performance.csv",
            mime="text/csv",
        )
        
        if st.button("اعتماد وضخ البيانات الخام إلى BigQuery", type="primary"):
            with st.spinner("جاري الضخ إلى المستودع..."):
                upload_data_to_bigquery(st.session_state["preview_m"])
                st.success("تم ضخ البيانات الخام بنجاح إلى BigQuery وجاهزة للخطوة التالية!")
