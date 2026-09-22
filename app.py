import os
import streamlit as st
import pandas as pd
from google.cloud import documentai_v1 as documentai
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="مرصد المطالبات | المعاينة والاعتماد", page_icon="📊", layout="wide")

PROJECT_ID = "claims-intelligence-507611"
LOCATION = "us"
PROCESSOR_ID = "YOUR_DOCUMENT_AI_PROCESSOR_ID"  # استبدل بـ Processor ID الخاص بك
DATASET_ID = "claims_intelligence"
TABLE_ID = "monthly_performance"

@st.cache_resource
def get_gcp_clients():
    creds_dict = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_dict:
        pk = creds_dict["private_key"].replace("\\n", "\n")
        creds_dict["private_key"] = pk
    credentials = Credentials.from_service_account_info(creds_dict)
    
    docai_client = documentai.DocumentProcessorServiceClient(credentials=credentials)
    bq_client = bigquery.Client(credentials=credentials, project=PROJECT_ID)
    return docai_client, bq_client

def process_scanned_pdf_preview(file_bytes, file_name, session_id):
    docai_client, _ = get_gcp_clients()
    
    name = docai_client.processor_path(PROJECT_ID, LOCATION, PROCESSOR_ID)
    raw_document = documentai.RawDocument(content=file_bytes, mime_type="application/pdf")
    request = documentai.ProcessRequest(name=name, raw_document=raw_document)
    
    result = docai_client.process_document(request=request)
    document = result.document

    extracted_rows = []
    for page in document.pages:
        page_number = int(page.page_number)
        page_text = ""
        for segment in page.layout.text_anchor.text_segments:
            start_index = int(segment.start_index) if segment.start_index else 0
            end_index = int(segment.end_index)
            page_text += document.text[start_index:end_index]
        
        extracted_rows.append({
            "session_id": str(session_id),
            "created_at": pd.Timestamp.now(tz='UTC').isoformat(),
            "source_file": file_name,
            "page_number": page_number,
            "extracted_text": page_text.strip()
        })
    
    return extracted_rows

st.title("مرصد المطالبات التأمينية | مرحلة الفحص والمعاينة")
st.markdown("ارفع تقرير المطالبات لمعالجته عبر الذكاء الاصطناعي ومعاينته بالكامل قبل اعتماده في مستودع البيانات.")

uploaded_file = st.file_uploader("رفع تقرير المطالبات (Scanned PDF)", type=["pdf"])

if uploaded_file:
    session_id = f"session_preview_{os.urandom(4).hex()}"
    
    if st.button("بدء المعالجة واستخراج البيانات للمعاينة", type="secondary"):
        with st.spinner("جاري تحليل المستند عبر Google Cloud Document AI..."):
            try:
                file_bytes = uploaded_file.read()
                rows = process_scanned_pdf_preview(file_bytes, uploaded_file.name, session_id)
                st.session_state["preview_df"] = pd.DataFrame(rows)
                st.success(f"تمت معالجة المستند بنجاح! تم استخراج {len(rows)} صفحة ومراجعتها أدناه.")
            except Exception as e:
                st.error(f"فشلت عملية المعالجة: {str(e)}")

    if "preview_df" in st.session_state and not st.session_state["preview_df"].empty:
        st.subheader("🔍 جدول معاينة البيانات المستخرجة (Review Before Ingestion)")
        st.dataframe(st.session_state["preview_df"], use_container_width=True)
        
        csv_data = st.session_state["preview_df"].to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 تحميل البيانات المستخرجة (CSV)",
            data=csv_data,
            file_name="extracted_claims_preview.csv",
            mime="text/csv",
        )
        
        if st.button("اعتماد وضخ البيانات نهائياً إلى BigQuery", type="primary"):
            with st.spinner("جاري ضخ البيانات المعتمدة إلى المستودع..."):
                try:
                    _, bq_client = get_gcp_clients()
                    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
                    records_to_push = st.session_state["preview_df"].to_dict(orient="records")
                    errors = bq_client.insert_rows_json(table_ref, records_to_push)
                    
                    if errors == []:
                        st.success("تم اعتماد وضخ البيانات بنجاح إلى BigQuery! أصبحت اللوحة التفاعلية جاهزة.")
                    else:
                        st.error(f"حدث خطأ أثناء الضخ: {errors}")
                except Exception as e:
                    st.error(f"فشل الاتصال بمستودع البيانات: {str(e)}")
