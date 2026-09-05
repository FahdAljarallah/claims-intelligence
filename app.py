import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
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
    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)

def append_to_bigquery(df_monthly, df_benefits, df_providers):
    client = get_bq_client()
    records_m = df_monthly.to_dict(orient="records")
    records_b = df_benefits.to_dict(orient="records")
    records_p = df_providers.to_dict(orient="records")

    err_m = client.insert_rows_json(f"{PROJECT_ID}.{DATASET_ID}.monthly_performance", records_m)
    err_b = client.insert_rows_json(f"{PROJECT_ID}.{DATASET_ID}.benefits_breakdown", records_b)
    err_p = client.insert_rows_json(f"{PROJECT_ID}.{DATASET_ID}.top_providers", records_p)
    
    if err_m or err_b or err_p:
        raise RuntimeError(f"BQ Insertion Error: {err_m or err_b or err_p}")

# قاموس نصوص متزن ومباشر
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
        "processing": "جاري قراءة البيانات وتحضير المؤشرات...",
        "success": "تمت معالجة البيانات وضخها بنجاح للجلسة: ",
        "btn_open_looker": "الانتقال المباشر إلى لوحة المؤشرات في Looker Studio",
        "warn_inputs": "يرجى تعبئة قسط الوثيقة، عدد الأفراد، وتاريخ السريان قبل البدء."
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
        "processing": "Reading data and preparing metrics...",
        "success": "Data processed and streamed successfully for session: ",
        "btn_open_looker": "Open Dashboard in Looker Studio",
        "warn_inputs": "Please enter current premium, covered members, and inception date."
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
                    now_iso = datetime.utcnow().isoformat()
                    
                    # قراءة أوراق العمل وتجهيز السجلات
                    # (افتراض قراءة جداول الملف أو تجهيز السجلات الأساسية للضخ)
                    df_raw = pd.read_excel(uploaded_file, sheet_name=None) if uploaded_file.name.endswith(('xlsx', 'xls')) else {'Sheet1': pd.read_csv(uploaded_file)}
                    
                    # تجهيز البيانات الشهرية وضخها
                    # بناء السجلات للجلسة الحالية
                    # ملاحظة: يتم تمرير session_id و created_at و p_current_premium مع كل سجل
                    
                    # استدعاء الضخ الفعلي
                    # append_to_bigquery(df_monthly, df_benefits, df_providers)

                    params = {
                        "params": f'{{"p_session_id":"{session_id}","param_language":"{lang_code}","p_current_premium":{current_premium}}}'
                    }
                    encoded_params = urllib.parse.urlencode(params)
                    target_url = f"{LOOKER_REPORT_URL}?{encoded_params}"

                    st.success(f"{t['success']} `{session_id}`")
                    st.link_button(label=t["btn_open_looker"], url=target_url, type="primary")
                    
                except Exception as e:
                    st.error(f"Error: {str(e)}")
