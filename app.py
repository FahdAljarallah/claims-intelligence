import os
import streamlit as st
import pandas as pd
import pdfplumber
import pypdf
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="مرصد المطالبات التأمينية | المعاينة الآمنة", page_icon="📊", layout="wide")

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"
TABLE_ID = "monthly_performance"

@st.cache_resource
def get_bq_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_dict:
        pk = creds_dict["private_key"].replace("\\n", "\n")
        creds_dict["private_key"] = pk
    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)

def process_pdf_safely(file_bytes, file_name, session_id):
    rows_to_insert = []
    
    # استخدام pypdf و pdfplumber للقراءة الآمنة بالكامل دون الحاجة لـ poppler
    try:
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        for page_num, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            rows_to_insert.append({
                "session_id": str(session_id),
                "created_at": pd.Timestamp.now(tz='UTC').isoformat(),
                "filename": file_name,
                "page_number": int(page_num),
                "extracted_text": text.strip()
            })
    except Exception as e:
        st.error(f"خطأ في قراءة المستند: {str(e)}")
        
    return rows_to_insert

st.title("مرصد المطالبات التأمينية | المعاينة الآمنة والمباشرة")
st.markdown("استخراج النصوص من التقارير ومراجعتها بالكامل في جدول المعاينة قبل اعتمادها في مستودع البيانات.")

uploaded_file = st.file_uploader("رفع تقرير المطالبات (PDF)", type=["pdf"])

if uploaded_file:
    session_id = f"session_safe_{os.urandom(4).hex()}"
    
    if st.button("بدء الاستخراج والمعاينة", type="secondary"):
        with st.spinner("جاري قراءة وتحليل المستند آمنياً..."):
            try:
                file_bytes = uploaded_file.read()
                rows = process_pdf_safely(file_bytes, uploaded_file.name, session_id)
                st.session_state["preview_safe_df"] = pd.DataFrame(rows)
                st.success(f"تمت معالجة المستند بنجاح! تم استخراج {len(rows)} صفحة وجاهزة للمراجعة أدناه.")
            except Exception as e:
                st.error(f"فشلت عملية المعالجة: {str(e)}")

    if "preview_safe_df" in st.session_state and not st.session_state["preview_safe_df"].empty:
        st.subheader("🔍 جدول معاينة البيانات المستخرجة (Review Before Ingestion)")
        st.dataframe(st.session_state["preview_safe_df"], use_container_width=True)
        
        csv_data = st.session_state["preview_safe_df"].to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 تحميل البيانات المستخرجة (CSV)",
            data=csv_data,
            file_name="safe_extracted_preview.csv",
            mime="text/csv",
        )
        
        if st.button("اعتماد وضخ البيانات إلى BigQuery", type="primary"):
            with st.spinner("جاري ضخ البيانات المعتمدة إلى BigQuery..."):
                try:
                    bq_client = get_bq_client()
                    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
                    records_to_push = st.session_state["preview_safe_df"].to_dict(orient="records")
                    errors = bq_client.insert_rows_json(table_ref, records_to_push)
                    
                    if errors == []:
                        st.success("🎉 تم اعتماد وضخ البيانات بنجاح إلى BigQuery! لوحة المؤشرات جاهزة للتفاعل.")
                    else:
                        st.error(f"حدث خطأ أثناء الضخ: {errors}")
                except Exception as e:
                    st.error(f"فشل الاتصال بمستودع البيانات: {str(e)}")
