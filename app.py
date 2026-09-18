import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import json
import urllib.parse
import time
import re
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

EXACT_BQ_COLUMNS_MONTHLY = [
    'session_id', 'created_at', 'policy_year', 'policy_year_label', 'month_code', 
    'month_weight', 'class_tier', 'active_lives', 'claims_count', 
    'paid_claims_sar', 'paid_claims_vat_sar'
]

EXACT_BQ_COLUMNS_BENEFITS = [
    'session_id', 'created_at', 'policy_year', 'policy_year_label', 'class_tier', 
    'benefit_name', 'claims_count', 'paid_claims_sar', 'paid_claims_vat_sar'
]

EXACT_BQ_COLUMNS_PROVIDERS = [
    'session_id', 'created_at', 'policy_year', 'policy_year_label', 'class_tier', 
    'provider_name', 'claims_count', 'paid_claims_sar', 'paid_claims_vat_sar'
]

def safe_clean_number(val):
    if pd.isna(val) or val is None:
        return 0.0
    val_str = str(val).replace('SAR', '').replace('ر.س', '').replace(',', '').strip()
    if val_str.count('.') > 1:
        parts = val_str.rsplit('.', 1)
        val_str = parts[0].replace('.', '') + '.' + parts[1]
    match = re.search(r'[-+]?\d*\.?\d+', val_str)
    return float(match.group()) if match else 0.0

# دالة استخراج مرنة ومرتكزة على البحث بالمفاتيح لتفادي اختلاف هيكلة الشركات (التعاونية وميدغلف)
def parse_pdf_claims_flexible(file_obj, session_id, default_members):
    file_obj.seek(0)
    cleaned_records = []
    with pdfplumber.open(file_obj) as pdf:
        current_tier = "CLASS VIP"
        for page in pdf.pages:
            page_text = page.extract_text()
            if not page_text:
                continue
            lines = page_text.split('\n')
            
            for line in lines:
                l_low = line.lower()
                if "class type" in l_low or "class" in l_low:
                    if "vip1" in l_low:
                        current_tier = "CLASS VIP1"
                    elif "vip" in l_low:
                        current_tier = "CLASS VIP"

            for line in lines:
                line_clean = line.strip()
                if not line_clean or any(kw in line_clean.lower() for kw in ['report date', 'total', 'subtotal', 'period', 'limit', 'classification', 'policy holder']):
                    continue
                
                # البحث عن أنماط الشهور بصيغة MM/YYYY أو MM-YYYY
                date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b', line_clean)
                if date_match:
                    month_num = date_match.group(1).zfill(2)
                    year_num = date_match.group(2)
                    month_code = f"{year_num}-{month_num}"
                    
                    tokens = [t.strip() for t in line_clean.split() if t.strip()]
                    numeric_vals = []
                    for t in tokens:
                        cleaned_val = safe_clean_number(t)
                        if t != date_match.group(0) and t != year_num:
                            numeric_vals.append(cleaned_val)
                    
                    # استخراج الأعمدة بمرونة تامة لضمان عدم انقلاب الأرقام
                    if len(numeric_vals) >= 3:
                        # التحقق من وجود رقم تسلسلي في البداية
                        if len(numeric_vals) >= 5 and numeric_vals[0] in list(range(1, 32)):
                            lives = numeric_vals[1]
                            claims_cnt = numeric_vals[2]
                            amt_before = numeric_vals[3]
                            amt_after = numeric_vals[4] if len(numeric_vals) > 4 else amt_before
                        else:
                            lives = numeric_vals[0]
                            claims_cnt = numeric_vals[1]
                            amt_before = numeric_vals[2]
                            amt_after = numeric_vals[3] if len(numeric_vals) > 3 else amt_before
                        
                        if claims_cnt > 0 or amt_before > 0:
                            cleaned_records.append({
                                'session_id': str(session_id),
                                'created_at': pd.Timestamp.now(tz='UTC'),
                                'policy_year': 'RAW_TEST',
                                'policy_year_label': file_obj.name,
                                'month_code': month_code,
                                'month_weight': 1.0,
                                'class_tier': current_tier,
                                'active_lives': int(lives) if lives > 5 else (int(default_members) if default_members else 100),
                                'claims_count': int(claims_cnt),
                                'paid_claims_sar': float(amt_before),
                                'paid_claims_vat_sar': float(amt_after)
                            })
    return cleaned_records

def parse_pdf_benefits(file_obj, session_id):
    file_obj.seek(0)
    benefit_records = []
    flexible_keywords = ['outpatient', 'inpatient', 'dental', 'optical', 'maternity', 'coverage', 'lab', 'consult', 'pharmacy']
    with pdfplumber.open(file_obj) as pdf:
        current_tier = "CLASS VIP"
        is_benefit_section = False
        for page in pdf.pages:
            page_text = page.extract_text()
            if not page_text:
                continue
            lines = page_text.split('\n')
            for line in lines:
                l_low = line.lower()
                l_nospace = l_low.replace(" ", "")
                if "class type" in l_low or "class" in l_low:
                    if "vip1" in l_low:
                        current_tier = "CLASS VIP1"
                    elif "vip" in l_low:
                        current_tier = "CLASS VIP"
                if "breakdown" in l_nospace:
                    is_benefit_section = True
                elif "top20" in l_nospace or "monthlyclaims" in l_nospace:
                    is_benefit_section = False
                    continue
                if is_benefit_section:
                    line_clean = line.strip()
                    if not line_clean or 'total' in l_nospace:
                        continue
                    if not any(kw in l_nospace for kw in flexible_keywords):
                        continue
                    tokens = [t.strip() for t in line_clean.split() if t.strip()]
                    numeric_tokens = [safe_clean_number(t) for t in tokens if safe_clean_number(t) > 0 or t == '0']
                    if len(numeric_tokens) >= 3:
                        benefit_name = tokens[0]
                        benefit_records.append({
                            'session_id': str(session_id),
                            'created_at': pd.Timestamp.now(tz='UTC'),
                            'policy_year': 'RAW_TEST',
                            'policy_year_label': file_obj.name,
                            'class_tier': current_tier,
                            'benefit_name': benefit_name,
                            'claims_count': int(numeric_tokens[0]),
                            'paid_claims_sar': numeric_tokens[1],
                            'paid_claims_vat_sar': numeric_tokens[2]
                        })
    return benefit_records

def parse_pdf_providers(file_obj, session_id):
    file_obj.seek(0)
    provider_records = []
    with pdfplumber.open(file_obj) as pdf:
        current_tier = "CLASS VIP"
        is_provider_section = False
        for page in pdf.pages:
            page_text = page.extract_text()
            if not page_text:
                continue
            lines = page_text.split('\n')
            for line in lines:
                l_low = line.lower()
                l_nospace = l_low.replace(" ", "")
                if "class type" in l_low or "class" in l_low:
                    if "vip1" in l_low:
                        current_tier = "CLASS VIP1"
                    elif "vip" in l_low:
                        current_tier = "CLASS VIP"
                if "top20" in l_nospace:
                    is_provider_section = True
                    continue
                elif "monthlyclaims" in l_nospace or "breakdown" in l_nospace:
                    is_provider_section = False
                    continue
                if is_provider_section:
                    line_clean = line.strip()
                    if not line_clean or any(kw in l_nospace for kw in ['providername', 'total', 'page', 'classification']):
                        continue
                    tokens = [t.strip() for t in line_clean.split() if t.strip()]
                    numeric_tokens = [safe_clean_number(t) for t in tokens if safe_clean_number(t) > 0 or t == '0']
                    if len(numeric_tokens) >= 3:
                        prov_name = " ".join([t for t in tokens if not re.search(r'\d', t)])
                        if len(prov_name) > 3:
                            provider_records.append({
                                'session_id': str(session_id),
                                'created_at': pd.Timestamp.now(tz='UTC'),
                                'policy_year': 'RAW_TEST',
                                'policy_year_label': file_obj.name,
                                'class_tier': current_tier,
                                'provider_name': prov_name,
                                'claims_count': int(numeric_tokens[0]),
                                'paid_claims_sar': numeric_tokens[1],
                                'paid_claims_vat_sar': numeric_tokens[2]
                            })
    return provider_records

def process_preview_files(uploaded_files, session_id, default_members):
    all_m, all_b, all_p = [], [], []
    for f in uploaded_files:
        if f.name.lower().endswith('.pdf'):
            all_m.extend(parse_pdf_claims_flexible(f, session_id, default_members))
            all_b.extend(parse_pdf_benefits(f, session_id))
            all_p.extend(parse_pdf_providers(f, session_id))
            
    return pd.DataFrame(all_m), pd.DataFrame(all_b), pd.DataFrame(all_p)

def upload_data_to_bigquery(df_monthly, df_benefits, df_providers):
    client = get_bq_client()
    datasets_map = {
        "monthly_performance": df_monthly,
        "benefits_breakdown": df_benefits,
        "top_providers": df_providers
    }
    for table_name, df_data in datasets_map.items():
        if df_data.empty:
            continue
        table_ref = f"{PROJECT_ID}.{DATASET_ID}.{table_name}"
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
            schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
            autodetect=True
        )
        job = client.load_table_from_dataframe(df_data, table_ref, job_config=job_config)
        job.result()

def delete_session_data(target_session_id):
    client = get_bq_client()
    for t in ["monthly_performance", "benefits_breakdown", "top_providers"]:
        try:
            query = f"DELETE FROM `{PROJECT_ID}.{DATASET_ID}.{t}` WHERE session_id = @sid"
            job_config = bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", target_session_id)]
            )
            client.query(query, job_config=job_config).result()
        except Exception:
            pass

i18n = {
    "AR": {
        "title": "مرصد المطالبات | محطة المعاينة التجريبية",
        "subtitle": "قم برفع ملفات PDF (التعاونية أو ميدغلف) لمعاينة دقة الأعمدة قبل الضخ النهائي.",
        "date_label": "تاريخ بداية سريان الوثيقة",
        "prem_label": "قسط الوثيقة السنوي الحالي (SAR)",
        "members_label": "إجمالي عدد المؤمن عليهم (Lives)",
        "upload_label": "رفع ملفات تجربة المطالبات (PDF)",
        "btn_preview": "معاينة البيانات المستخرجة",
        "btn_push": "اعتماد وضخ البيانات إلى BigQuery",
        "processing": "جاري معالجة المستندات...",
        "success": "تم ضخ البيانات بنجاح للجلسة: ",
        "warn_inputs": "يرجى تعبئة الحقول الأساسية.",
        "btn_end_session": "إنهاء الجلسة وحذف البيانات",
        "session_cleared": "تم حذف بيانات الجلسة بنجاح."
    }
}

selected_lang = st.selectbox("Language / اللغة", options=["العربية", "English"], index=0)
lang_code = "AR" if selected_lang == "العربية" else "EN"
t = i18n[lang_code]

st.title(t["title"])
st.markdown(t["subtitle"])

col_date, col_members = st.columns(2)
with col_date:
    inception_date = st.date_input(t["date_label"], value=None)
with col_members:
    total_members = st.number_input(t["members_label"], min_value=1, max_value=1000000, value=None, step=1)

col_prem, _ = st.columns(2)
with col_prem:
    current_premium = st.number_input(t["prem_label"], min_value=1000.0, max_value=500000000.0, value=None, step=50000.0, format="%.2f")

uploaded_files = st.file_uploader(t["upload_label"], type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    
    if st.button(t["btn_preview"], type="secondary"):
        if not current_premium or not total_members or not inception_date:
            st.warning(t["warn_inputs"])
        else:
            with st.spinner(t["processing"]):
                df_m, df_b, df_p = process_preview_files(uploaded_files, session_id, total_members)
                st.session_state["preview_m"] = df_m
                st.session_state["preview_b"] = df_b
                st.session_state["preview_p"] = df_p
                st.session_state["temp_session_id"] = session_id
                st.success("تم استخراج البيانات وجاهزة للمعاينة أدناه!")

    if "preview_m" in st.session_state and not st.session_state["preview_m"].empty:
        st.subheader("🔍 معاينة جدول الأداء الشهري (Monthly Performance Preview)")
        st.dataframe(st.session_state["preview_m"], use_container_width=True)
        
        st.subheader("🔍 معاينة جدول المنافع (Benefits Breakdown Preview)")
        st.dataframe(st.session_state["preview_b"], use_container_width=True)

        if st.button(t["btn_push"], type="primary"):
            with st.spinner("جاري الضخ إلى المستودع..."):
                upload_data_to_bigquery(
                    st.session_state["preview_m"], 
                    st.session_state["preview_b"], 
                    st.session_state["preview_p"]
                )
                st.session_state["active_session_id"] = st.session_state["temp_session_id"]
                
                url_params = {
                    "ds14.p_session_id": st.session_state["active_session_id"],
                    "ds15.p_session_id": st.session_state["active_session_id"],
                    "ds16.p_session_id": st.session_state["active_session_id"],
                    "ds14.param_language": lang_code,
                    "ds14.p_current_premium": int(current_premium),
                    "ds14.p_target_census": int(total_members),
                    "ts": int(time.time())
                }
                encoded_params = urllib.parse.urlencode({"params": json.dumps(url_params)})
                base_view_url = LOOKER_REPORT_URL.replace("/edit", "/view")
                target_url = f"{base_view_url}?{encoded_params}"

                st.success(f"{t['success']} `{st.session_state['active_session_id']}`")
                st.link_button(label="الانتقال إلى لوحة المؤشرات النهائية", url=target_url, type="primary")

if "active_session_id" in st.session_state:
    st.divider()
    if st.button(t["btn_end_session"], type="secondary"):
        delete_session_data(st.session_state["active_session_id"])
        del st.session_state["active_session_id"]
        st.success(t["session_cleared"])
        st.rerun()
