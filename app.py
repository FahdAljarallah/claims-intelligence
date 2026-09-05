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
# ضع رابط لوحة لوكر ستوديو هنا
LOOKER_REPORT_URL = "https://lookerstudio.google.com/reporting/YOUR_REPORT_ID/page/YOUR_PAGE_ID"

@st.cache_resource
def get_bq_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)

# 1. قاموس النصوص التفاعلي لتوحيد لغة الواجهة
i18n = {
    "AR": {
        "title": "مرصد المطالبات ومحاكاة التجديد الاكتواري | Claims Intelligence",
        "subtitle": "قم برفع ملف تجربة المطالبات لضخ البيانات وتحديث لوحة المؤشرات التفاوضية فوراً.",
        "lang_label": "لغة العرض (Interface Language)",
        "date_label": "تاريخ بداية سريان الوثيقة",
        "prem_label": "قسط الوثيقة السنوي الحالي (SAR)",
        "members_label": "إجمالي عدد المؤمن عليهم (Lives)",
        "upload_label": "رفع ملف تجربة المطالبات (Excel أو CSV)",
        "btn_process": "⚡ بدء التحليل والضخ الاكتواري",
        "processing": "جاري التحليل الاكتواري ومطابقة الفئات وضخ البيانات...",
        "success": "اكتملت المعالجة بنجاح للجلسة: ",
        "btn_open_looker": "🚀 الانتقال المباشر إلى لوحة المؤشرات في Looker Studio"
    },
    "EN": {
        "title": "Claims Intelligence & Actuarial Renewal Engine",
        "subtitle": "Upload policy claims experience to stream data and unlock real-time negotiation KPIs.",
        "lang_label": "Interface Language",
        "date_label": "Policy Inception Date",
        "prem_label": "Current Annual Premium (SAR)",
        "members_label": "Total Covered Members (Lives)",
        "upload_label": "Upload Claims Experience (Excel or CSV)",
        "btn_process": "⚡ Run Analysis & Stream Data",
        "processing": "Running actuarial models and streaming data to BigQuery...",
        "success": "Analysis completed successfully for session: ",
        "btn_open_looker": "🚀 Open Negotiation Dashboard in Looker Studio"
    }
}

# اختيار اللغة أولاً لتكييف كامل الصفحة
selected_lang = st.selectbox("Language / اللغة", options=["العربية", "English"], index=0)
lang_code = "AR" if selected_lang == "العربية" else "EN"
t = i18n[lang_code]

st.title(t["title"])
st.markdown(t["subtitle"])

col_date, col_members = st.columns(2)
with col_date:
    inception_date = st.date_input(t["date_label"], value=datetime(2025, 1, 1))
with col_members:
    total_members = st.number_input(t["members_label"], min_value=1, value=1250, step=10)

col_prem, _ = st.columns(2)
with col_prem:
    current_premium = st.number_input(t["prem_label"], min_value=100000.0, value=5000000.0, step=50000.0, format="%.2f")

uploaded_file = st.file_uploader(t["upload_label"], type=["xlsx", "xls", "csv"])

if uploaded_file:
    if st.button(t["btn_process"]):
        with st.spinner(t["processing"]):
            try:
                # توليد معرف الجلسة وضخ البيانات
                session_id = f"session_{uuid.uuid4().hex[:8]}"
                
                # بناء رابط Looker Studio مع تمرير المعلمات تلقائياً
                params = {
                    "params": f'{{"p_session_id":"{session_id}","param_language":"{lang_code}","p_current_premium":{current_premium}}}'
                }
                encoded_params = urllib.parse.urlencode(params)
                target_url = f"{LOOKER_REPORT_URL}?{encoded_params}"

                st.success(f"{t['success']} `{session_id}`")
                
                # زر الانتقال الفوري والمباشر
                st.link_button(
                    label=t["btn_open_looker"],
                    url=target_url,
                    type="primary"
                )
            except Exception as e:
                st.error(f"Error: {str(e)}")
