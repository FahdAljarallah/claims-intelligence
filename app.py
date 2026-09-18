import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import json
import urllib.parse
import re
import pdfplumber
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

# 1. إعداد واجهة التطبيق
st.set_page_config(
    page_title="Claims Intelligence Portal",
    page_icon="📊",
    layout="wide"
)

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"
LOOKER_REPORT_URL = "https://lookerstudio.google.com/reporting/34329d81-4adf-410e-86a9-24713511ec47/page/1f97F"

# 2. إدارة الاتصال بمستودع بيانات BigQuery
@st.cache_resource
def get_bq_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_dict:
        pk = creds_dict["private_key"].replace("\\n", "\n")
        if "-----BEGIN PRIVATE KEY-----" not in pk:
            clean_body = pk.replace("-----BEGIN PRIVATE KEY-----", "").replace("-----END PRIVATE KEY-----", "").strip()
            if clean_body.startswith("nMI"):
                clean_body = clean_body[1:]
            pk = f"-----BEGIN PRIVATE KEY-----\n{clean_body}\n-----END PRIVATE KEY-----\n"
        creds_dict["private_key"] = pk
    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)

# الأعمدة الصارمة المطابقة لمخططات الجداول
EXACT_BQ_COLUMNS_MONTHLY = [
    'session_id', 'created_at', 'policy_year', 'policy_year_label', 'month_code', 
    'month_weight', 'class_tier', 'active_lives', 'claims_count', 
    'paid_claims_sar', 'paid_claims_vat_sar'
]

EXACT_BQ_COLUMNS_BENEFITS = [
    'session_id', 'created_at', 'policy_year', 'policy_year_label', 'class_tier', 
    'benefit_name', 'claims_count', 'paid_claims_sar', 'paid_claims_vat_sar'
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

# 3. استخراج البيانات الشهرية من الـ PDF
def parse_pdf_claims(file_obj, session_id, default_members):
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
                if any(kw in line_clean.lower() for kw in ['report date', 'total', 'subtotal', 'period', 'limit']):
                    continue
                date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b', line_clean)
                if date_match:
                    month_num = date_match.group(1).zfill(2)
                    year_num = date_match.group(2)
                    std_month_code = f"{year_num}-{month_num}"
                    line_without_date = line_clean.replace(date_match.group(0), '')
                    tokens = [t.strip() for t in line_without_date.split() if t.strip()]
                    numeric_values = [safe_clean_number(t) for t in tokens if safe_clean_number(t) > 0 or t == '0']
                    
                    if len(numeric_values) >= 8:
                        lives, claims_cnt, amt_before_vat, amt_after_vat = numeric_values[1], numeric_values[2], numeric_values[3], numeric_values[4]
                    elif len(numeric_values) == 7:
                        lives, claims_cnt, amt_before_vat, amt_after_vat = numeric_values[0], numeric_values[1], numeric_values[2], numeric_values[3]
                    elif len(numeric_values) == 5:
                        lives, claims_cnt, amt_before_vat, amt_after_vat = numeric_values[1], numeric_values[2], numeric_values[3], numeric_values[4]
                    elif len(numeric_values) == 4:
                        lives, claims_cnt, amt_before_vat, amt_after_vat = numeric_values[0], numeric_values[1], numeric_values[2], numeric_values[3]
                    elif len(numeric_values) == 3:
                        lives, claims_cnt, amt_before_vat, amt_after_vat = 0, numeric_values[0], numeric_values[1], numeric_values[2]
                    else:
                        continue
                        
                    if claims_cnt > 0 or amt_before_vat > 0:
                        cleaned_records.append({
                            'session_id': str(session_id),
                            'created_at': pd.Timestamp.now(tz='UTC'),
                            'month_code': std_month_code,
                            'month_weight': 1,
                            'class_tier': current_tier,
                            'active_lives': int(lives) if lives > 0 else (int(default_members) if default_members else 100),
                            'claims_count': int(claims_cnt),
                            'paid_claims_sar': amt_before_vat,
                            'paid_claims_vat_sar': amt_after_vat
                        })
    return cleaned_records

# 4. استخراج جدول المنافع (Breakdown by Benefit) من الـ PDF بدقة
def parse_pdf_benefits(file_obj, session_id):
    benefit_records = []
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
                if "class type" in l_low or "class" in l_low:
                    if "vip1" in l_low:
                        current_tier = "CLASS VIP1"
                    elif "vip" in l_low:
                        current_tier = "CLASS VIP"
                
                if "breakdown by benefit" in l_low or "breakdown by benefits" in l_low:
                    is_benefit_section = True
                    continue
                elif "top 20 utilised" in l_low or "monthly claims" in l_low:
                    is_benefit_section = False

                if is_benefit_section:
                    line_clean = line.strip()
                    if any(kw in line_clean.lower() for kw in ['benefit', 'total', 'page', 'classification', 'last policy year', 'pyr']):
                        continue
                    tokens = [t.strip() for t in line_clean.split() if t.strip()]
                    numeric_tokens = [safe_clean_number(t) for t in tokens if safe_clean_number(t) > 0 or t == '0']
                    if len(numeric_tokens) >= 3:
                        benefit_name = " ".join([t for t in tokens if not re.search(r'\d', t)])
                        if not benefit_name:
                            benefit_name = tokens[0]
                        benefit_records.append({
                            'session_id': str(session_id),
                            'created_at': pd.Timestamp.now(tz='UTC'),
                            'class_tier': current_tier,
                            'benefit_name': benefit_name,
                            'claims_count': int(numeric_tokens[0]),
                            'paid_claims_sar': numeric_tokens[1],
                            'paid_claims_vat_sar': numeric_tokens[2]
                        })
    return benefit_records

# 5. معالجة وتوزيع السنوات التعاقدية
def process_all_files(uploaded_files, session_id, default_members):
    all_monthly = []
    all_benefits = []

    for f in uploaded_files:
        if f.name.lower().endswith('.pdf'):
            m_rec = parse_pdf_claims(f, session_id, default_members)
            b_rec = parse_pdf_benefits(f, session_id)
            all_monthly.extend(m_rec)
            all_benefits.extend(b_rec)

    if not all_monthly:
        return pd.DataFrame(columns=EXACT_BQ_COLUMNS_MONTHLY), pd.DataFrame(columns=EXACT_BQ_COLUMNS_BENEFITS)

    df_monthly = pd.DataFrame(all_monthly)
    df_monthly = df_monthly.groupby(['session_id', 'month_code', 'class_tier'], as_index=False).agg({
        'created_at': 'first', 'month_weight': 'first', 'active_lives': 'max',
        'claims_count': 'sum', 'paid_claims_sar': 'sum', 'paid_claims_vat_sar': 'sum'
    })

    df_monthly['period_date'] = pd.to_datetime(df_monthly['month_code'], format='%Y-%m')
    df_monthly = df_monthly.sort_values('period_date').reset_index(drop=True)
    
    df_monthly['cycle_base_year'] = df_monthly['period_date'].apply(
        lambda d: d.year if d.month == 12 else d.year - 1
    )
    df_monthly['policy_year_label'] = df_monthly['cycle_base_year'].astype(str) + " / " + (df_monthly['cycle_base_year'] + 1).astype(str)
    
    max_year = df_monthly['cycle_base_year'].max()
    df_monthly['policy_year'] = df_monthly['cycle_base_year'].apply(
        lambda y: 'CY' if y == max_year else ('PY' if y == max_year - 1 else 'P2Y')
    )
    
    # ربط ملصقات السنوات بجدول المنافع بناءً على أحدث دورة أو مطابقة الفترة
    df_benefits = pd.DataFrame(all_benefits) if all_benefits else pd.DataFrame(columns=EXACT_BQ_COLUMNS_BENEFITS)
    if not df_benefits.empty:
        df_benefits['policy_year'] = 'CY'
        df_benefits['policy_year_label'] = df_monthly['policy_year_label'].iloc[-1]
        df_benefits = df_benefits[EXACT_BQ_COLUMNS_BENEFITS]

    df_monthly = df_monthly.drop(columns=['period_date', 'cycle_base_year'])
    return df_monthly[EXACT_BQ_COLUMNS_MONTHLY], df_benefits

# 6. الرفع لـ BigQuery
def upload_data_to_bigquery(df_monthly, df_benefits):
    client = get_bq_client()
    datasets_map = {
        "monthly_performance": df_monthly,
        "benefits_breakdown": df_benefits
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

# 7. واجهة المستخدم
i18n = {
    "AR": {
        "title": "مرصد المطالبات ومحاكاة التجديد | Claims Intelligence",
        "subtitle": "قم برفع ملف تجربة المطالبات لقراءة الأداء وتحديث لوحة المؤشرات فوراً.",
        "lang_label": "اللغة / Language",
        "date_label": "تاريخ بداية سريان الوثيقة",
        "prem_label": "قسط الوثيقة السنوي الحالي (SAR)",
        "members_label": "إجمالي عدد المؤمن عليهم (Lives)",
        "upload_label": "رفع ملفات تجربة المطالبات (PDF)",
        "btn_process": "قراءة وتحليل البيانات",
        "processing": "جاري معالجة البيانات وضخها للمستودع...",
        "success": "تمت معالجة البيانات بنجاح للجلسة: ",
        "btn_open_looker": "الانتقال المباشر إلى لوحة المؤشرات في Looker Studio",
        "warn_inputs": "يرجى تعبئة قسط الوثيقة، عدد الأفراد، وتاريخ السريان.",
        "session_mgmt": "إدارة وحوكمة الجلسة",
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
    if st.button(t["btn_process"], type="primary"):
        if not current_premium or not total_members or not inception_date:
            st.warning(t["warn_inputs"])
        else:
            with st.spinner(t["processing"]):
                try:
                    session_id = f"session_{uuid.uuid4().hex[:8]}"
                    st.session_state["active_session_id"] = session_id

                    df_monthly, df_benefits = process_all_files(uploaded_files, session_id, total_members)
                    
                    if df_monthly.empty:
                        raise ValueError("لم يتم العثور على أسطر مطالبات صالحة.")

                    upload_data_to_bigquery(df_monthly, df_benefits)

                    url_params = {
                        "ds14.p_session_id": session_id,
                        "ds15.p_session_id": session_id,
                        "ds16.p_session_id": session_id,
                        "ds14.param_language": lang_code,
                        "ds14.p_current_premium": int(current_premium),
                        "ds14.p_target_census": int(total_members)
                    }

                    encoded_params = urllib.parse.urlencode({"params": json.dumps(url_params)})
                    target_url = f"{LOOKER_REPORT_URL}?{encoded_params}"

                    st.success(f"{t['success']} `{session_id}`")
                    st.link_button(label=t["btn_open_looker"], url=target_url, type="primary")

                except Exception as e:
                    st.error(f"حدث خطأ: {str(e)}")

if "active_session_id" in st.session_state:
    st.divider()
    if st.button(t["btn_end_session"], type="secondary"):
        delete_session_data(st.session_state["active_session_id"])
        del st.session_state["active_session_id"]
        st.success(t["session_cleared"])
        st.rerun()
