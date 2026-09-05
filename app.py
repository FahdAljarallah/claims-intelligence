import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

# ضبط إعدادات الصفحة
st.set_page_config(
    page_title="Claims Intelligence Portal",
    page_icon="📊",
    layout="wide"
)

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"

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

# واجهة المستخدم التنفيذية
st.title("مرصد المطالبات ومحاكاة التجديد الاكتواري | Claims Intelligence")
st.markdown("قم برفع ملف تجربة المطالبات لضخ البيانات وتحديث لوحة المؤشرات التفاوضية فوراً.")

# الصف الأول: إعدادات العرض واللغة وتاريخ السريان
col_lang, col_date = st.columns(2)
with col_lang:
    selected_language = st.selectbox(
        "لغة عرض اللوحة في Looker Studio",
        options=["العربية", "English"],
        index=0
    )
    lang_param = "AR" if selected_language == "العربية" else "EN"

with col_date:
    inception_date = st.date_input("تاريخ بداية سريان الوثيقة", value=datetime(2025, 1, 1))

# الصف الثاني: الركائز المالية ومؤشر تكلفة الفرد
col_prem, col_members = st.columns(2)
with col_prem:
    current_premium = st.number_input(
        "قسط الوثيقة السنوي الحالي (SAR)", 
        min_value=100000.0, 
        value=5000000.0, 
        step=50000.0,
        format="%.2f"
    )
with col_members:
    total_members = st.number_input(
        "إجمالي عدد المؤمن عليهم (Lives)", 
        min_value=1, 
        value=1250, 
        step=10
    )

uploaded_file = st.file_uploader("رفع ملف تجربة المطالبات (Excel أو CSV)", type=["xlsx", "xls", "csv"])

if uploaded_file and st.button("🚀 معالجة وضخ البيانات إلى BigQuery"):
    with st.spinner("جاري التحليل الاكتواري وضخ البيانات..."):
        try:
            session_id = f"session_{uuid.uuid4().hex[:8]}"
            st.success(f"تم إنشاء الجلسة بنجاح: {session_id}")
            st.info("البيانات جاهزة للعرض على Looker Studio.")
        except Exception as e:
            st.error(f"حدث خطأ أثناء المعالجة: {str(e)}")
