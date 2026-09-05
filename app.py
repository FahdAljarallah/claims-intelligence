import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import json
import urllib.parse
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="Claims Intelligence Portal",
    page_icon="📊",
    layout="wide"
)

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
            pk = f"-----BEGIN PRIVATE KEY-----\n{clean_body}\n-----END PRIVATE KEY-----\n"
        creds_dict["private_key"] = pk
    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)

# الأعمدة المعتمدة فعلياً داخل جدول BigQuery
EXACT_BQ_COLUMNS = [
    'session_id', 'created_at', 'policy_year', 'month_code', 
    'month_weight', 'class_tier', 'active_lives', 'claims_count', 
    'paid_claims_sar', 'paid_claims_vat_sar'
]

COLUMN_MAPPING = {
    'policy_year': ['policy_year', 'year', 'سنة', 'السنة'],
    'month_code': ['month_code', 'month', 'شهر', 'الشهر', 'period'],
    'month_weight': ['month_weight', 'month_no', 'ترتيب'],
    'class_tier': ['class_tier', 'class', 'فئة', 'tier'],
    'active_lives': ['active_lives', 'lives', 'members', 'الأعضاء', 'المؤمن عليهم'],
    'claims_count': ['claims_count', 'count', 'عدد المطالبات', 'مطالبات'],
    'paid_claims_sar': ['paid_claims_sar', 'net_paid', 'paid', 'claim_amount', 'المطالبات', 'المبلغ'],
    'paid_claims_vat_sar': ['paid_claims_vat_sar', 'gross_paid', 'vat', 'الإجمالي']
}

def map_and_clean_df(df, session_id, default_members):
    df_clean = df.copy()
    df_clean.columns = [str(c).strip().lower().replace(" ", "_").replace("/", "_") for c in df_clean.columns]
    
    mapped_data = pd.DataFrame()
    mapped_data['session_id'] = [str(session_id)] * len(df_clean)
    mapped_data['created_at'] = pd.Timestamp.now(tz='UTC')

    for target_col, variations in COLUMN_MAPPING.items():
        found = False
        for v in variations:
            if v in df_clean.columns:
                mapped_data[target_col] = df_clean[v]
                found = True
                break
        if not found:
            if target_col == 'active_lives':
                mapped_data[target_col] = int(default_members) if default_members else 100
            elif target_col == 'policy_year':
                mapped_data[target_col] = str(datetime.now().year)
            elif target_col in ['paid_claims_sar', 'claims_count', 'month_weight', 'paid_claims_vat_sar']:
                mapped_data[target_col] = 0
            else:
                mapped_data[target_col] = "General"

    # تنظيف المبالغ الرقمية
    for num_col in ['paid_claims_sar', 'paid_claims_vat_sar', 'active_lives', 'claims_count', 'month_weight']:
        mapped_data[num_col] = mapped_data[num_col].astype(str).str.replace(',', '').str.replace('SAR', '').str.strip()
        mapped_data[num_col] = pd.to_numeric(mapped_data[num_col], errors='coerce').fillna(0)

    # حصر وتصفية الأعمدة بدقة على ما هو موجود في BigQuery فقط دون أي حقل زائد
    return mapped_data[EXACT_BQ_COLUMNS]

def append_to_bigquery_free_tier(df_mapped):
    client = get_bq_client()
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.monthly_performance"
    
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        autodetect=False  # إيقاف الاكتشاف التلقائي لضمان مطابقة الـ Schema الحالية
    )
    job = client.load_table_from_dataframe(df_mapped, table_ref, job_config=job_config)
    job.result()

def delete_session_data(target_session_id):
    client = get_bq_client()
    tables = ["monthly_performance", "benefits_breakdown", "top_providers"]
    for t in tables:
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
        "title": "مرصد المطالبات ومحاكاة التجديد | Claims Intelligence",
        "subtitle": "قم برفع ملف تجربة المطالبات لقراءة الأداء وتحديث لوحة المؤشرات فوراً.",
        "lang_label": "اللغة / Language",
        "date_label": "تاريخ بداية سريان الوثيقة",
        "prem_label": "قسط الوثيقة السنوي الحالي (SAR)",
        "members_label": "إجمالي عدد المؤمن عليهم (Lives)",
        "upload_label": "رفع ملف تجربة المطالبات (Excel أو CSV)",
        "btn_process": "قراءة وتحليل البيانات",
        "processing": "جاري فحص ومطابقة الأعمدة وضخ البيانات...",
        "success": "تمت معالجة البيانات وضخها بنجاح للجلسة: ",
        "btn_open_looker": "الانتقال المباشر إلى لوحة المؤشرات في Looker Studio",
        "warn_inputs": "يرجى تعبئة قسط الوثيقة، عدد الأفراد، وتاريخ السريان.",
        "session_mgmt": "إدارة وحوكمة الجلسة",
        "btn_end_session": "إنهاء الجلسة وحذف البيانات",
        "session_cleared": "تم حذف بيانات الجلسة بنجاح وتطهير السجلات."
    },
    "EN": {
        "title": "Claims Intelligence & Renewal Dashboard",
        "subtitle": "Upload policy claims experience to analyze performance and update metrics.",
        "lang_label": "Language / اللغة",
        "date_label": "Policy Inception Date",
        "prem_label": "Current Annual Premium (SAR)",
        "members_label": "Total Covered Members (Lives)",
        "upload_label": "Upload Claims Experience (Excel or CSV)",
        "btn_process": "Process Data",
        "processing": "Mapping columns and streaming clean records...",
        "success": "Data processed successfully for session: ",
        "btn_open_looker": "Open Dashboard in Looker Studio",
        "warn_inputs": "Please enter current premium, covered members, and inception date.",
        "session_mgmt": "Session Governance",
        "btn_end_session": "End Session & Delete Data",
        "session_cleared": "Session data deleted and purged successfully."
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
    total_members = st.number_input(
        t["members_label"], 
        min_value=1, 
        max_value=1000000, 
        value=None, 
        step=1,
        placeholder="مثال: 1,250"
    )

col_prem, _ = st.columns(2)
with col_prem:
    current_premium = st.number_input(
        t["prem_label"], 
        min_value=1000.0, 
        max_value=500000000.0, 
        value=None, 
        step=50000.0,
        format="%.2f",
        placeholder="مثال: 4,500,000.00"
    )

if current_premium:
    st.caption(f"SAR {current_premium:,.2f}")

uploaded_file = st.file_uploader(t["upload_label"], type=["xlsx", "xls", "csv"])

if uploaded_file:
    if st.button(t["btn_process"]):
        if not current_premium or not total_members or not inception_date:
            st.warning(t["warn_inputs"])
        else:
            with st.spinner(t["processing"]):
                try:
                    session_id = f"session_{uuid.uuid4().hex[:8]}"
                    st.session_state["active_session_id"] = session_id

                    if uploaded_file.name.endswith(('xlsx', 'xls')):
                        excel_dict = pd.read_excel(uploaded_file, sheet_name=None)
                        first_sheet = list(excel_dict.keys())[0]
                        df_raw = excel_dict[first_sheet]
                    else:
                        df_raw = pd.read_csv(uploaded_file)

                    # مطابقة الأعمدة الصريحة وحصرها بدقة
                    df_mapped = map_and_clean_df(df_raw, session_id, total_members)

                    # ضخ البيانات المتطابقة تماماً مع BigQuery
                    append_to_bigquery_free_tier(df_mapped)

                    url_params = {
                        "ds14.p_session_id": session_id,
                        "ds14.param_language": lang_code,
                        "ds14.p_current_premium": int(current_premium),
                        "ds14.p_target_census": int(total_members)
                    }

                    encoded_params = urllib.parse.urlencode({"params": json.dumps(url_params)})
                    target_url = f"{LOOKER_REPORT_URL}?{encoded_params}"

                    st.success(f"{t['success']} `{session_id}`")
                    st.link_button(label=t["btn_open_looker"], url=target_url, type="primary")

                except Exception as e:
                    st.error(f"حدث خطأ أثناء معالجة وضخ الملف: {str(e)}")

if "active_session_id" in st.session_state:
    st.divider()
    st.subheader(t["session_mgmt"])
    col_s1, col_s2 = st.columns([3, 1])
    with col_s1:
        st.info(f"الجلسة النشطة الحالية: `{st.session_state['active_session_id']}`")
    with col_s2:
        if st.button(t["btn_end_session"], type="secondary"):
            with st.spinner("جاري حذف البيانات..."):
                try:
                    delete_session_data(st.session_state["active_session_id"])
                    del st.session_state["active_session_id"]
                    st.success(t["session_cleared"])
                    st.rerun()
                except Exception as ex:
                    st.error(f"تعذر حذف البيانات: {str(ex)}")
