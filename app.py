import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import json
import urllib.parse
import re
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

EXACT_BQ_COLUMNS = [
    'session_id', 'created_at', 'policy_year', 'policy_year_label', 'month_code', 
    'month_weight', 'class_tier', 'active_lives', 'claims_count', 
    'paid_claims_sar', 'paid_claims_vat_sar'
]

def safe_clean_number(val):
    if pd.isna(val) or val is None:
        return 0.0
    val_str = str(val).replace(',', '').replace('SAR', '').replace('ر.س', '').strip()
    match = re.search(r'[-+]?\d*\.?\d+', val_str)
    return float(match.group()) if match else 0.0

def parse_pdf_claims(file_obj, session_id, default_members):
    """استخراج مصفوفة الأداء الشهري وتفكيك التواريخ من صفحات الـ PDF"""
    cleaned_records = []
    current_period_tag = "CY"
    detected_class = "VIP"

    with pdfplumber.open(file_obj) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            text_lower = page.extract_text().lower() if page.extract_text() else ""
            
            # فحص فئة الوثيقة
            if "vip" in text_lower:
                detected_class = "VIP"
            elif "class a" in text_lower:
                detected_class = "Class A"
            elif "class b" in text_lower:
                detected_class = "Class B"

            # فحص السنة التعاقدية (التعاونية تقسم السنتين في صفحات منفصلة عادة: ص1 وص3)
            if "last policy year" in text_lower or page_idx == 0:
                current_period_tag = "CY"
            elif "prior policy year" in text_lower or "prior year" in text_lower or page_idx >= 2:
                current_period_tag = "PY"

            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or len(row) < 5:
                        continue
                    
                    row_clean = [str(c).strip().replace('\n', ' ') if c is not None else '' for c in row]
                    cell_0 = row_clean[0]
                    cell_1 = row_clean[1] if len(row_clean) > 1 else ''

                    # رصد كود الشهر بصيغة MM/YYYY (مثل 12/2024 أو 01/2025)
                    date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2}|\d{2})\b', cell_0) or \
                                 re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2}|\d{2})\b', cell_1)

                    if date_match:
                        raw_date = date_match.group()
                        is_c0_date = (raw_date == cell_0)
                        
                        # استخراج الأجزاء لتشكيل YYYY-MM المتوافق
                        parts = re.split(r'[\/\-]', raw_date)
                        month_part = parts[0].zfill(2)
                        year_part = parts[1] if len(parts[1]) == 4 else f"20{parts[1]}"
                        std_month_code = f"{year_part}-{month_part}"

                        lives = safe_clean_number(row_clean[1] if is_c0_date else row_clean[2])
                        claims_cnt = safe_clean_number(row_clean[2] if is_c0_date else row_clean[3])
                        paid_amt = safe_clean_number(row_clean[3] if is_c0_date else row_clean[4])
                        paid_vat = safe_clean_number(row_clean[4] if is_c0_date and len(row_clean) > 4 else (row_clean[5] if len(row_clean) > 5 else paid_amt))

                        cleaned_records.append({
                            'session_id': str(session_id),
                            'created_at': pd.Timestamp.now(tz='UTC'),
                            'policy_year': str(current_period_tag),
                            'month_code': std_month_code,
                            'month_weight': 1,
                            'class_tier': str(detected_class),
                            'active_lives': int(lives) if lives > 0 else (int(default_members) if default_members else 100),
                            'claims_count': int(claims_cnt),
                            'paid_claims_sar': paid_amt,
                            'paid_claims_vat_sar': paid_vat
                        })

    return cleaned_records

def parse_raw_insurance_report(file_obj, session_id, default_members):
    # مسار ملفات PDF
    if file_obj.name.lower().endswith('.pdf'):
        records = parse_pdf_claims(file_obj, session_id, default_members)
        df_result = pd.DataFrame(records)
        if not df_result.empty:
            min_months = df_result.groupby('policy_year')['month_code'].transform('min')
            df_result['policy_year_label'] = min_months.apply(lambda m: f"{str(m)[:4]} / {int(str(m)[:4]) + 1}")
            return df_result[EXACT_BQ_COLUMNS]
        return pd.DataFrame(columns=EXACT_BQ_COLUMNS)

    # مسار ملفات Excel و CSV
    if file_obj.name.endswith(('xlsx', 'xls')):
        excel_data = pd.read_excel(file_obj, sheet_name=None, header=None)
        target_sheet = list(excel_data.keys())[0]
        for name in excel_data.keys():
            if 'month' in name.lower():
                target_sheet = name
                break
        raw_df = excel_data[target_sheet]
    else:
        raw_df = pd.read_csv(file_obj, header=None)

    # 1. استخراج فئة الوثيقة التعاقدية
    detected_class = "Class A"
    for idx, row in raw_df.head(10).iterrows():
        for c_idx, val in enumerate(row.values):
            if pd.notnull(val) and 'product/class' in str(val).lower():
                if c_idx + 1 < len(row.values) and pd.notnull(row.values[c_idx + 1]):
                    detected_class = f"Class {str(row.values[c_idx + 1]).strip()}"
                    break

    # 2. رصد ترويسة الجدول
    header_idx = 10
    for idx, row in raw_df.head(25).iterrows():
        row_str = " ".join([str(val).lower() for val in row.values if pd.notnull(val)])
        if 'monthly claims' in row_str and 'paid claims' in row_str:
            header_idx = idx
            break

    data_rows = raw_df.iloc[header_idx + 1:].copy()
    first_col_idx = 0

    cleaned_records = []
    current_period_tag = "P2Y"

    # 3. قراءة البيانات وحصر الشهور
    for _, row in data_rows.iterrows():
        cell_val = str(row.iloc[first_col_idx]).strip()
        cell_lower = cell_val.lower()

        if 'policy year' in cell_lower or 'prior' in cell_lower or 'last' in cell_lower:
            if '2 years prior' in cell_lower:
                current_period_tag = "P2Y"
            elif 'prior policy year' in cell_lower or 'prior year' in cell_lower:
                current_period_tag = "PY"
            elif 'last policy year' in cell_lower or 'current' in cell_lower:
                current_period_tag = "CY"
            continue

        if 'total' in cell_lower or cell_lower in ['nan', 'none', '']:
            continue

        raw_code = cell_val.replace('.0', '')
        if raw_code.isdigit() and len(raw_code) == 6:
            lives = safe_clean_number(row.iloc[1])
            claims_cnt = safe_clean_number(row.iloc[2])
            paid_amt = safe_clean_number(row.iloc[3])
            paid_vat = safe_clean_number(row.iloc[4])

            cleaned_records.append({
                'session_id': str(session_id),
                'created_at': pd.Timestamp.now(tz='UTC'),
                'policy_year': str(current_period_tag),
                'month_code': f"{raw_code[:4]}-{raw_code[4:]}",
                'month_weight': 1,
                'class_tier': str(detected_class),
                'active_lives': int(lives) if lives > 0 else (int(default_members) if default_members else 100),
                'claims_count': int(claims_cnt),
                'paid_claims_sar': paid_amt,
                'paid_claims_vat_sar': paid_vat
            })

    df_result = pd.DataFrame(cleaned_records)
    if not df_result.empty:
        min_months = df_result.groupby('policy_year')['month_code'].transform('min')
        df_result['policy_year_label'] = min_months.apply(lambda m: f"{str(m)[:4]} / {int(str(m)[:4]) + 1}")
        return df_result[EXACT_BQ_COLUMNS]
    
    return pd.DataFrame(columns=EXACT_BQ_COLUMNS)

def append_to_bigquery_free_tier(df_mapped):
    if df_mapped.empty:
        return
    client = get_bq_client()
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.monthly_performance"
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
        autodetect=True
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
        "upload_label": "رفع ملف تجربة المطالبات (PDF أو Excel أو CSV)",
        "btn_process": "قراءة وتحليل البيانات",
        "processing": "جاري استخراج الفترات والسنوات الاكتوارية تلقائياً...",
        "success": "تمت معالجة وضخ البيانات بنجاح للجلسة: ",
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
        "upload_label": "Upload Claims Experience (PDF, Excel, or CSV)",
        "btn_process": "Process Data",
        "processing": "Automatically deriving dynamic policy year labels...",
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

                    df_mapped = parse_raw_insurance_report(uploaded_file, session_id, total_members)
                    
                    if df_mapped.empty:
                        raise ValueError("لم يتم العثور على أسطر مطالبات صالحة داخل الملف.")

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
                    st.error(f"حدث خطأ أثناء معالجة الملف: {str(e)}")

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
