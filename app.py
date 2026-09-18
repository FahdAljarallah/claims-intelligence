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

# 1. إعداد واجهة التطبيق
st.set_page_config(
    page_title="Claims Intelligence Portal",
    page_icon="📊",
    layout="wide"
)

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"
LOOKER_REPORT_URL = "https://lookerstudio.google.com/reporting/34329d81-4adf-410e-86a9-24713511ec47/page/1f97F"

# 2. إدارة الاتصال بمستودع بيانات BigQuery
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

# الأعمدة الصارمة المطابقة لمخطط جدول BigQuery
EXACT_BQ_COLUMNS = [
    'session_id', 'created_at', 'policy_year', 'policy_year_label', 'month_code', 
    'month_weight', 'class_tier', 'active_lives', 'claims_count', 
    'paid_claims_sar', 'paid_claims_vat_sar'
]

def safe_clean_number(val):
    """تنظيف النصوص الرقمية والتعامل مع الفواصل والنقاط المكررة بدقة"""
    if pd.isna(val) or val is None:
        return 0.0
    val_str = str(val).replace('SAR', '').replace('ر.س', '').replace(',', '').strip()
    if val_str.count('.') > 1:
        parts = val_str.rsplit('.', 1)
        val_str = parts[0].replace('.', '') + '.' + parts[1]
    match = re.search(r'[-+]?\d*\.?\d+', val_str)
    return float(match.group()) if match else 0.0

# 3. محرك استخراج البيانات من ملفات الـ PDF (التعاونية وميدغلف)
def parse_pdf_claims(file_obj, session_id, default_members):
    cleaned_records = []
    
    with pdfplumber.open(file_obj) as pdf:
        current_tier = "CLASS VIP"
        
        for page in pdf.pages:
            page_text = page.extract_text()
            if not page_text:
                continue
            
            lines = page_text.split('\n')
            
            for line in lines:
                l_low = line.lower()
                if "class type" in l_low or "class" in l_low:
                    if "vip1" in l_low:
                        current_tier = "CLASS VIP1"
                    elif "vip" in l_low:
                        current_tier = "CLASS VIP"

            for line in lines:
                line_clean = line.strip()
                if any(kw in line_clean.lower() for kw in ['report date', 'total', 'subtotal', 'period', 'limit']):
                    continue
                
                # رصد نمط الشهر والسنة (MM/YYYY)
                date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b', line_clean)
                if date_match:
                    month_num = date_match.group(1).zfill(2)
                    year_num = date_match.group(2)
                    std_month_code = f"{year_num}-{month_num}"
                    
                    line_without_date = line_clean.replace(date_match.group(0), '')
                    tokens = [t.strip() for t in line_without_date.split() if t.strip()]
                    numeric_values = [safe_clean_number(t) for t in tokens if safe_clean_number(t) > 0 or t == '0']
                    
                    # استخراج الأعمدة حسب هيكلة الجدول ومنع سحب مبالغ الـ Outstanding الصفرية
                    if len(numeric_values) >= 8:
                        lives = numeric_values[1]
                        claims_cnt = numeric_values[2]
                        amt_before_vat = numeric_values[3]
                        amt_after_vat = numeric_values[4]
                    elif len(numeric_values) == 7:
                        lives = numeric_values[0]
                        claims_cnt = numeric_values[1]
                        amt_before_vat = numeric_values[2]
                        amt_after_vat = numeric_values[3]
                    elif len(numeric_values) == 5:
                        lives = numeric_values[1]
                        claims_cnt = numeric_values[2]
                        amt_before_vat = numeric_values[3]
                        amt_after_vat = numeric_values[4]
                    elif len(numeric_values) == 4:
                        lives = numeric_values[0]
                        claims_cnt = numeric_values[1]
                        amt_before_vat = numeric_values[2]
                        amt_after_vat = numeric_values[3]
                    elif len(numeric_values) == 3:
                        lives = 0
                        claims_cnt = numeric_values[0]
                        amt_before_vat = numeric_values[1]
                        amt_after_vat = numeric_values[2]
                    else:
                        continue
                        
                    if claims_cnt > 0 or amt_before_vat > 0:
                        cleaned_records.append({
                            'session_id': str(session_id),
                            'created_at': pd.Timestamp.now(tz='UTC'),
                            'month_code': std_month_code,
                            'month_weight': 1,
                            'class_tier': current_tier,
                            'active_lives': int(lives) if lives > 0 else (int(default_members) if default_members else 100),
                            'claims_count': int(claims_cnt),
                            'paid_claims_sar': amt_before_vat,
                            'paid_claims_vat_sar': amt_after_vat
                        })

    return cleaned_records

# 4. محرك استخراج البيانات من ملفات Excel و CSV
def parse_excel_or_csv(file_obj, session_id, default_members):
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

    detected_class = "Class A"
    header_idx = 10
    for idx, row in raw_df.head(25).iterrows():
        row_str = " ".join([str(val).lower() for val in row.values if pd.notnull(val)])
        if 'monthly claims' in row_str and 'paid claims' in row_str:
            header_idx = idx
            break

    data_rows = raw_df.iloc[header_idx + 1:].copy()
    cleaned_records = []

    for _, row in data_rows.iterrows():
        cell_val = str(row.iloc[0]).strip()
        cell_lower = cell_val.lower()

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
                'month_code': f"{raw_code[:4]}-{raw_code[4:]}",
                'month_weight': 1,
                'class_tier': detected_class,
                'active_lives': int(lives) if lives > 0 else (int(default_members) if default_members else 100),
                'claims_count': int(claims_cnt),
                'paid_claims_sar': min(paid_amt, paid_vat),
                'paid_claims_vat_sar': max(paid_amt, paid_vat)
            })

    return cleaned_records

# 5. منطق فصل الدورات التعاقدية وعزل السنوات (CY vs PY) استناداً إلى أرقام الشهور الفعلية
def process_all_files(uploaded_files, session_id, default_members):
    all_records = []
    for f in uploaded_files:
        if f.name.lower().endswith('.pdf'):
            records = parse_pdf_claims(f, session_id, default_members)
        else:
            records = parse_excel_or_csv(f, session_id, default_members)
        all_records.extend(records)

    if not all_records:
        return pd.DataFrame(columns=EXACT_BQ_COLUMNS)

    df = pd.DataFrame(all_records)
    
    # تجميع الفئات لنفس الشهر وجمع المبالغ المالية
    df = df.groupby(['session_id', 'month_code', 'class_tier'], as_index=False).agg({
        'created_at': 'first',
        'month_weight': 'first',
        'active_lives': 'max',
        'claims_count': 'sum',
        'paid_claims_sar': 'sum',
        'paid_claims_vat_sar': 'sum'
    })

    # تحويل كود الشهر لتاريخ للفرز الدقيق
    df['period_date'] = pd.to_datetime(df['month_code'], format='%Y-%m')
    df = df.sort_values('period_date').reset_index(drop=True)
    
    # تحديد دورة السنة التعاقدية: الشهور (12 حتى 11 من السنة التالية) تنتمي لسنة البداية
    df['cycle_base_year'] = df['period_date'].apply(
        lambda d: d.year if d.month == 12 else d.year - 1
    )
    
    # إسناد ملصق السنة التعاقدية الموحد (2024 / 2025)
    df['policy_year_label'] = df['cycle_base_year'].astype(str) + " / " + (df['cycle_base_year'] + 1).astype(str)
    
    # تصنيف الدورات ديناميكياً: أحدث دورة CY، والسابقة PY، والأقدم P2Y
    max_year = df['cycle_base_year'].max()
    df['policy_year'] = df['cycle_base_year'].apply(
        lambda y: 'CY' if y == max_year else ('PY' if y == max_year - 1 else 'P2Y')
    )
    
    df = df.drop(columns=['period_date', 'cycle_base_year'])
    return df[EXACT_BQ_COLUMNS]

# 6. الرفع إلى BigQuery وحوكمة الجلسة
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

# 7. قواميس النصوص والواجهة
i18n = {
    "AR": {
        "title": "مرصد المطالبات ومحاكاة التجديد | Claims Intelligence",
        "subtitle": "قم برفع ملف تجربة المطالبات لقراءة الأداء وتحديث لوحة المؤشرات فوراً.",
        "lang_label": "اللغة / Language",
        "date_label": "تاريخ بداية سريان الوثيقة",
        "prem_label": "قسط الوثيقة السنوي الحالي (SAR)",
        "members_label": "إجمالي عدد المؤمن عليهم (Lives)",
        "upload_label": "رفع ملفات تجربة المطالبات (PDF أو Excel أو CSV)",
        "btn_process": "قراءة وتحليل البيانات",
        "processing": "جاري سحب البيانات و تهيئتها...",
        "success": "تمت معالجة وضخ البيانات بنجاح للجلسة: ",
        "btn_open_looker": "الانتقال المباشر إلى لوحة المؤشرات في Looker Studio",
        "warn_inputs": "يرجى تعبئة قسط الوثيقة، عدد الأفراد، وتاريخ السريان.",
        "session_mgmt": "إدارة وحوكمة الجلسة",
        "btn_end_session": "إنهاء الجلسة وحذف البيانات من المستودع",
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
        "processing": "Ingesting and preparing data...",
        "success": "Data processed successfully for session: ",
        "btn_open_looker": "Open Dashboard in Looker Studio",
        "warn_inputs": "Please enter current premium, covered members, and inception date.",
        "session_mgmt": "Session Governance",
        "btn_end_session": "End Session & Purge Warehouse Data",
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

uploaded_files = st.file_uploader(
    t["upload_label"], 
    type=["pdf", "xlsx", "xls", "csv"], 
    accept_multiple_files=True
)

if uploaded_files:
    if st.button(t["btn_process"], type="primary"):
        if not current_premium or not total_members or not inception_date:
            st.warning(t["warn_inputs"])
        else:
            with st.spinner(t["processing"]):
                try:
                    session_id = f"session_{uuid.uuid4().hex[:8]}"
                    st.session_state["active_session_id"] = session_id

                    df_mapped = process_all_files(uploaded_files, session_id, total_members)
                    
                    if df_mapped.empty:
                        raise ValueError("لم يتم العثور على أسطر مطالبات صالحة داخل الملفات المرفوعة.")

                    append_to_bigquery_free_tier(df_mapped)

                    url_params = {
                        "ds14.p_session_id": session_id,
                        "ds14.param_language": lang_code,
                        "ds14.p_current_premium": int(current_premium),
                        "ds14.p_target_census": int(total_members)
                    }

                    encoded_params = urllib.parse.urlencode({"params": json.dumps(url_params)})
                    target_url = f"{LOOKER_REPORT_URL}?{encoded_params}"

                    st.success(f"{t['success']} `{session_id}` (تمت معالجة {len(df_mapped)} شهراً بنجاح)")
                    st.link_button(label=t["btn_open_looker"], url=target_url, type="primary")

                except Exception as e:
                    st.error(f"حدث خطأ أثناء معالجة الملف: {str(e)}")

# 8. قسم حوكمة الجلسة
if "active_session_id" in st.session_state:
    st.divider()
    st.subheader(t["session_mgmt"])
    col_s1, col_s2 = st.columns([3, 1])
    with col_s1:
        st.info(f"الجلسة النشطة الحالية: `{st.session_state['active_session_id']}`")
    with col_s2:
        if st.button(t["btn_end_session"], type="secondary"):
            with st.spinner("جاري تطهير البيانات من المستودع..."):
                try:
                    delete_session_data(st.session_state["active_session_id"])
                    del st.session_state["active_session_id"]
                    st.success(t["session_cleared"])
                    st.rerun()
                except Exception as ex:
                    st.error(f"تعذر حذف البيانات: {str(ex)}")
