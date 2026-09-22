import os
import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="مرصد المطالبات | المعالجة المحلية والمعاينة", page_icon="📊", layout="wide")

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

def process_scanned_pdf_locally(file_bytes, file_name, session_id):
    # 1. تحويل صفحات الـ PDF إلى صور في الذاكرة بدقة عالية
    pages = convert_from_bytes(file_bytes, dpi=300)
    
    rows_to_insert = []
    # 2. المرور على الصفحات وتطبيق المعالجة البصرية المحلية Tesseract
    for page_num, page_image in enumerate(pages, start=1):
        text = pytesseract.image_to_string(page_image)
        
        rows_to_insert.append({
            "session_id": str(session_id),
            "created_at": pd.Timestamp.now(tz='UTC').isoformat(),
            "filename": file_name,
            "page_number": int(page_num),
            "extracted_text": text.strip()
        })
        
    return rows_to_insert

st.title("مرصد المطالبات التأمينية | المعالجة المحلية المجانية والمعاينة")
st.markdown("استخراج النصوص محلياً من التقارير المصورة ومراجعتها بالكامل قبل اعتمادها في مستودع البيانات.")

uploaded_file = st.file_uploader("رفع تقرير المطالبات المصور (Scanned PDF)", type=["pdf"])

if uploaded_file:
    session_id = f"session_local_{os.urandom(4).hex()}"
    
    if st.button("بدء الاستخراج المحلي والمعاينة", type="secondary"):
        with st.spinner("جاري تحويل الصفحات واستخراج النصوص بصرياً (Local OCR)..."):
            try:
                file_bytes = uploaded_file.read()
                rows = process_scanned_pdf_locally(file_bytes, uploaded_file.name, session_id)
                st.session_state["preview_local_df"] = pd.DataFrame(rows)
                st.success(f"تمت معالجة المستند بنجاح! تم استخراج {len(rows)} صفحة وجاهزة للمراجعة أدناه.")
            except Exception as e:
                st.error(f"فشلت عملية المعالجة المحلية: {str(e)}")

    if "preview_local_df" in st.session_state and not st.session_state["preview_local_df"].empty:
        st.subheader("🔍 جدول معاينة البيانات المستخرجة محلياً (Review Before Ingestion)")
        st.dataframe(st.session_state["preview_local_df"], use_container_width=True)
        
        csv_data = st.session_state["preview_local_df"].to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 تحميل البيانات المستخرجة (CSV)",
            data=csv_data,
            file_name="local_extracted_preview.csv",
            mime="text/csv",
        )
        
        if st.button("اعتماد وضخ البيانات إلى BigQuery مجاناً", type="primary"):
            with st.spinner("جاري ضخ البيانات المعتمدة إلى BigQuery..."):
                try:
                    bq_client = get_bq_client()
                    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
                    records_to_push = st.session_state["preview_local_df"].to_dict(orient="records")
                    errors = bq_client.insert_rows_json(table_ref, records_to_push)
                    
                    if errors == []:
                        st.success("🎉 تم اعتماد وضخ البيانات بنجاح إلى BigQuery! لوحة المؤشرات جاهزة للتفاعل.")
                    else:
                        st.error(f"حدث خطأ أثناء الضخ: {errors}")
                except Exception as e:
                    st.error(f"فشل الاتصال بمستودع البيانات: {str(e)}")
