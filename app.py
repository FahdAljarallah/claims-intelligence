import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import urllib.parse
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

# 1. إعدادات الصفحة والهوية
st.set_page_config(
    page_title="Claims Intelligence Portal",
    page_icon="📊",
    layout="wide"
)

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"
# الرابط التشغيلي الفعلي للوحة Looker Studio
LOOKER_REPORT_URL = "https://lookerstudio.google.com/reporting/34329d81-4adf-410e-86a9-24713511ec47/page/1f97F"

@st.cache_resource
def get_bq_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)

# 2. مصفوفة الترجمة وتوحيد لغة الواجهة
i18n = {
    "AR": {
        "title": "مرصد المطالبات ومحاكاة التجديد الاكتواري | Claims Intelligence",
        "subtitle": "قم برفع ملف تجربة المطالبات لضخ البيانات وتحديث لوحة المؤشرات التفاوضية فوراً.",
        "lang_label": "اللغة / Language",
        "date_label": "تاريخ بداية سريان الوثيقة",
        "prem_label": "قسط الوثيقة السنوي الحالي (SAR)",
        "members_label": "إجمالي عدد المؤمن عليهم (Lives)",
        "upload_label": "رفع ملف تجربة المطالبات (Excel أو CSV)",
        "btn_process": "⚡ بدء التحليل والضخ الاكتواري",
        "processing": "جاري التحليل الاكتواري ومطابقة الفئات وضخ البيانات إلى BigQuery...",
        "success": "اكتملت المعالجة بنجاح للجلسة: ",
        "btn_open_looker": "🚀 الانتقال المباشر إلى لوحة المؤشرات في Looker Studio",
        "warn_inputs": "يرجى تعبئة قسط الوثيقة، عدد الأفراد، وتاريخ السريان قبل المعالجة."
    },
    "EN": {
        "title": "Claims Intelligence & Actuarial Renewal Engine",
        "subtitle": "Upload policy claims experience to stream data and unlock real-time negotiation KPIs.",
        "lang_label": "Language / اللغة",
        "date_label": "Policy Inception Date",
        "prem_label": "Current Annual Premium (SAR)",
        "members_label": "Total Covered Members (Lives)",
        "upload_label": "Upload Claims Experience (Excel or CSV)",
        "btn_process": "⚡ Run Analysis & Stream Data",
        "processing": "Running actuarial models and streaming data to BigQuery...",
        "success": "Analysis completed successfully for session: ",
        "btn_open_looker": "🚀 Open Negotiation Dashboard in Looker Studio",
        "warn_inputs": "Please enter current premium, covered members, and inception date before proceeding."
    }
}

# اختيار لغة الواجهة
selected_lang = st.selectbox("Language / اللغة", options=["العربية", "English"], index=0)
lang_code = "AR" if selected_lang == "العربية" else "EN"
t = i18n[lang_code]

st.title(t["title"])
st.markdown(t["subtitle"])

# الحقول فارغة افتراضياً لفرض الإدخال الدقيق
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

# معاينة فورية لتنسيق المبلغ بالفواصل الألفية
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
                    # توليد معرف الجلسة
                    session_id = f"session_{uuid.uuid4().hex[:8]}"
                    
                    # بناء الرابط المشفر مع تمرير المعلمات إلى Looker Studio
                    params = {
                        "params": f'{{"p_session_id":"{session_id}","param_language":"{lang_code}","p_current_premium":{current_premium}}}'
                    }
                    encoded_params = urllib.parse.urlencode(params)
                    target_url = f"{LOOKER_REPORT_URL}?{encoded_params}"

                    st.success(f"{t['success']} `{session_id}`")
                    
                    # زر الانتقال الفوري المباشر للوحة
                    st.link_button(
                        label=t["btn_open_looker"],
                        url=target_url,
                        type="primary"
                    )
                except Exception as e:
                    st.error(f"Error: {str(e)}")
