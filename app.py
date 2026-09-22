import os
import streamlit as st
import pandas as pd
from google.cloud import documentai_v1 as documentai
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Claims Intelligence | Document AI Portal", page_icon="📑", layout="wide")

PROJECT_ID = "claims-intelligence-507611"
LOCATION = "us"  # أو المنطقة المخصصة للمزود (مثل eu)
PROCESSOR_ID = "YOUR_DOCUMENT_AI_PROCESSOR_ID"  # معرف الـ Processor الخاص بك في Google Cloud
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

def process_scanned_pdf_via_document_ai(file_bytes, file_name, session_id):
    docai_client, bq_client = get_gcp_clients()
    
    # إعداد طلب Document AI (Layout Parser / OCR Processor)
    name = docai_client.processor_path(PROJECT_ID, LOCATION, PROCESSOR_ID)
    raw_document = documentai.RawDocument(content=file_bytes, mime_type="application/pdf")
    request = documentai.ProcessRequest(name=name, raw_document=raw_document)
    
    result = docai_client.process_document(request=request)
    document = result.document

    rows_to_insert = []
    for page in document.pages:
        page_number = int(page.page_number)
        page_text = ""
        for segment in page.layout.text_anchor.text_segments:
            start_index = int(segment.start_index) if segment.start_index else 0
            end_index = int(segment.end_index)
            page_text += document.text[start_index:end_index]
        
        rows_to_insert.append({
            "session_id": str(session_id),
            "created_at": pd.Timestamp.now(tz='UTC').isoformat(),
            "source_file": file_name,
            "page_number": page_number,
            "extracted_text": page_text.strip()
        })
    
    return rows_to_insert

st.title("مرصد المطالبات | محرك Document AI المؤسسي")
st.markdown("استخراج ذكي ومتقدم للجداول والوثائق المصورة عبر ذكاء Google Cloud الاصطناعي.")

uploaded_file = st.file_uploader("رفع تقرير المطالبات المصور (Scanned PDF)", type=["pdf"])

if uploaded_file:
    session_id = f"session_docai_{os.urandom(4).hex()}"
    if st.button("معالجة المستند عبر Document AI وضخه إلى BigQuery", type="primary"):
        with st.spinner("جاري تحليل بنية المستند واستخراج الجداول عبر Google Cloud..."):
            try:
                file_bytes = uploaded_file.read()
                extracted_rows = process_scanned_pdf_via_document_ai(file_bytes, uploaded_file.name, session_id)
                
                # ضخ البيانات إلى BigQuery
                table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
                bq_client = bigquery.Client(credentials=Credentials.from_service_account_info(dict(st.secrets["gcp_service_account"])), project=PROJECT_ID)
                errors = bq_client.insert_rows_json(table_ref, extracted_rows)
                
                if errors == []:
                    st.success(f"تم بنجاح معالجة {len(extracted_rows)} صفحة وضخها مباشرة إلى BigQuery بجاهزية تامة للتحليل التنفيذي!")
                    df_preview = pd.DataFrame(extracted_rows)
                    st.dataframe(df_preview, use_container_width=True)
                else:
                    st.error(f"حدث خطأ أثناء الضخ إلى BigQuery: {errors}")
            except Exception as e:
                st.error(f"فشلت عملية المعالجة: {str(e)}")
