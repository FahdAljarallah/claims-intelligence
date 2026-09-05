import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import json
import io
import urllib.parse
import pdfplumber
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

# استخراج الجداول من ملفات الـ PDF
def parse_pdf_claims(uploaded_pdf):
    extracted_tables = []
    with pdfplumber.open(uploaded_pdf) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for t in tables:
                if t and len(t) > 1:
                    df = pd.DataFrame(t[1:], columns=t[0])
                    extracted_tables.append(df)
    
    if not extracted_tables:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    
    # الجدول الأول عادة يمثل الأداء الشهري للمطالبات في تقارير شركات التأمين
    df_monthly = extracted_tables[0]
    df_benefits = extracted_tables[1] if len(extracted_tables) > 1 else pd.DataFrame()
    df_providers = extracted_tables[2] if len(extracted_tables) > 2 else pd.DataFrame()
    
    return df_monthly, df_benefits, df_providers

# مطابقة ورفع البيانات المتوافقة مجاناً مع Free Tier
def safe_load_to_bq(client, df, table_name):
    if df.empty:
        return
    
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{table_name}"
    df_clean = df.copy()
    
    # إزالة أي فهارس غير مسماة وتنظيف الرموز
    unnamed = [c for c in df_clean.columns if str(c).startswith("Unnamed:")]
    if unnamed:
        df_clean.drop(columns=unnamed, inplace=True)
        
    df_clean.columns = [str(c).strip().replace(" ", "_").replace("/", "_").replace("-", "_") for c in df_clean.columns]
    
    # تنظيف المبالغ المالية من أي نصوص أو فواصل لضمان قراءتها كأرقام في BigQuery
    for col in df_clean.columns:
        if any(k in col.lower() for k in ['claim', 'paid', 'amount', 'incurred', 'مطالبات', 'مبلغ']):
            try:
                df_clean[col] = df_clean[col].astype(str).str.replace(',', '').str.replace('SAR', '').str.strip()
                df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce').fillna(0.0)
            except Exception:
                pass

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        autodetect=True
    )
    job = client.load_table_from_dataframe(df_clean, table_ref, job_config=job_config)
    job.result()

def delete_session_data(target_session_id):
    client = get_bq_client()
    tables = ["monthly_performance", "benefits_breakdown", "top_providers"]
    for t in tables:
        query = f"DELETE FROM `{PROJECT_ID}.{DATASET_ID}.{t}` WHERE session_id = @sid"
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", target_session_id)]
        )
        client.query(query, job_config=job_config).result()

# الواجهة ثنائية اللغة
i18n = {
    "AR": {
        "title": "مرصد المطالبات ومحاكاة التجديد | Claims Intelligence",
        "subtitle": "قم برفع تقرير المطالبات (PDF أو Excel أو CSV) لقراءة الأداء وتحديث مؤشرات التفاوض فوراً.",
        "lang_label": "اللغة / Language",
        "date_label": "تاريخ بداية سريان الوثيقة",
        "prem_label": "قسط الوثيقة السنوي الحالي (SAR)",
        "members_label": "إجمالي عدد المؤمن عليهم (Lives)",
        "upload_label": "رفع ملف تجربة المطالبات (PDF / Excel / CSV)",
        "btn_process": "قراءة وتحليل البيانات",
        "processing": "جاري استخراج البيانات وضخ السجلات وتجهيز المؤشرات...",
        "success": "تمت معالجة البيانات وضخها بنجاح للجلسة: ",
        "btn_open_looker": "الانتقال المباشر إلى لوحة المؤشرات في Looker Studio",
        "warn_inputs": "يرجى تعبئة قسط الوثيقة، عدد الأفراد، وتاريخ السريان.",
        "session_mgmt": "إدارة وحوكمة الجلسة",
        "btn_end_session": "إنهاء الجلسة وحذف البيانات",
        "session_cleared": "تم حذف بيانات الجلسة وتطهير السجلات بنجاح."
    },
    "EN": {
        "title": "Claims Intelligence & Renewal Dashboard",
        "subtitle": "Upload claims experience report (PDF, Excel, CSV) to update negotiation KPIs.",
        "lang_label": "Language / اللغة",
        "date_label": "Policy Inception Date",
        "prem_label": "Current Annual Premium (SAR)",
        "members_label": "Total Covered Members (Lives)",
        "upload_label": "Upload Claims Experience (PDF / Excel / CSV)",
        "btn_process": "Process Data",
        "processing": "Extracting tables and streaming records...",
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

# دعم صيغ PDF و Excel و CSV صراحة
uploaded_file = st.file_uploader(t["upload_label"], type=["pdf", "xlsx", "xls", "csv"])

if uploaded_file:
    if st.button(t["btn_process"]):
        if not current_premium or not total_members or not inception_date:
            st.warning(t["warn_inputs"])
        else:
            with st.spinner(t["processing"]):
                try:
                    session_id = f"session_{uuid.uuid4().hex[:8]}"
                    st.session_state["active_session_id"] = session_id
                    now_ts = pd.Timestamp.now(tz='UTC')

                    file_ext = uploaded_file.name.split('.')[-1].lower()

                    if file_ext == 'pdf':
                        df_m, df_b, df_p = parse_pdf_claims(uploaded_file)
                    elif file_ext in ['xlsx', 'xls']:
                        excel_dict = pd.read_excel(uploaded_file, sheet_name=None)
                        sheet_names = list(excel_dict.keys())
                        df_m = excel_dict.get('Monthly', excel_dict.get('monthly_performance', excel_dict[sheet_names[0]]))
                        df_b = excel_dict.get('Benefits', excel_dict.get('benefits_breakdown', pd.DataFrame()))
                        df_p = excel_dict.get('Providers', excel_dict.get('top_providers', pd.DataFrame()))
                    else:
                        df_raw = pd.read_csv(uploaded_file)
                        df_m, df_b, df_p = df_raw, pd.DataFrame(), pd.DataFrame()

                    for df in [df_m, df_b, df_p]:
                        if not df.empty:
                            df['session_id'] = session_id
                            df['created_at'] = now_ts

                    client = get_bq_client()
                    safe_load_to_bq(client, df_m, "monthly_performance")
                    safe_load_to_bq(client, df_b, "benefits_breakdown")
                    safe_load_to_bq(client, df_p, "top_providers")

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
