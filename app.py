import io
import re
import uuid
import urllib.parse
import calendar
from datetime import datetime
import streamlit as st
import pandas as pd
import pdfplumber
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="Claims Experience Ingestor | استيعاب تقارير المطالبات",
    page_icon="🛡️",
    layout="centered"
)

# قاموس الواجهة المهني
TEXTS = {
    "ar": {
        "title": "📤 استيعاب وتحليل تقرير تجربة المطالبات",
        "subtitle": "معايرة اكتوارية متقدمة للأشهر العقدية مع عزل كامل للجلسة.",
        "policy_date_label": "تاريخ سريان الوثيقة الحالية (Policy Inception Date) *:",
        "date_warning": "يرجى تحديد تاريخ سريان الوثيقة للبدء في معالجة البيانات.",
        "expander_label": "⚙️ مدخلات متقدمة للتفاوض ومحاكاة التجديد (اختياري)",
        "premium_label": "قيمة قسط الوثيقة الحالية بالريال (Current Premium SAR):",
        "census_label": "تعداد المؤمن عليهم المستهدف للتجديد (Target Renewal Census):",
        "uploader_label": "اختر ملف تقرير المطالبات (Excel أو PDF):",
        "processing": "جارٍ معايرة الأوزان النسبية للأشهر، عزل الجلسة، وحقن البيانات...",
        "error_parse": "تعذر التعرف على جداول التقرير. يرجى التأكد من رفع التقرير المعتمد.",
        "error_api": "حدث خطأ أثناء تحديث مستودع البيانات. يرجى مراجعة الصلاحيات.",
        "success_msg": "تمت معالجة البيانات بنجاح! رمز جلستك المعزولة:",
        "btn_dashboard": "🚀 فتح لوحة التحليل والتفاوض",
        "btn_download": "📥 تحميل نسخة احتياطية مسطحة (Excel)",
        "btn_purge": "🔒 إنهاء جلسة التفاوض وحذف البيانات فوراً",
        "purging_msg": "جارٍ إتلاف سجلات الجلسة من خوادم المعالجة بأمان...",
        "purge_success": "تم إتلاف بيانات الجلسة بنجاح من قاعدة البيانات المركزية."
    },
    "en": {
        "title": "📤 Claims Experience Ingestor & Intelligence",
        "subtitle": "Actuarially normalized policy exposure with complete session isolation.",
        "policy_date_label": "Current Policy Inception Date *:",
        "date_warning": "Please select the policy inception date to proceed with processing.",
        "expander_label": "⚙️ Advanced Inputs for Renewal Negotiation (Optional)",
        "premium_label": "Current Policy Premium (SAR):",
        "census_label": "Target Renewal Census Count:",
        "uploader_label": "Upload Claims Experience report (Excel or PDF):",
        "processing": "Normalizing month weights, isolating session, and updating data layer...",
        "error_parse": "Failed to parse standardized tables. Please verify report format.",
        "error_api": "Error updating central data repository. Verify permissions.",
        "success_msg": "Data processed successfully! Isolated Session ID:",
        "btn_dashboard": "🚀 Launch Negotiation Dashboard",
        "btn_download": "📥 Download Clean Backup Data (Excel)",
        "btn_purge": "🔒 End Session & Purge Data Immediately",
        "purging_msg": "Securely purging session records from repository...",
        "purge_success": "Session data has been completely erased from the repository."
    }
}

with st.sidebar:
    lang_choice = st.radio("Language / اللغة", options=["العربية", "English"], index=0)
    lang = "ar" if lang_choice == "العربية" else "en"
    t = TEXTS[lang]

LOOKER_STUDIO_BASE_URL = "https://datastudio.google.com/reporting/34329d81-4adf-410e-86a9-24713511ec47/page/1f97F"

def generate_session_id():
    return f"SES_{uuid.uuid4().hex[:8].upper()}"

def clean_num(val):
    if pd.isna(val):
        return None
    try:
        cleaned = str(val).replace(',', '').strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return None

def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = dict(st.secrets["gcp_service_account"])
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(credentials)

def append_to_sheets(df_monthly, df_benefits, df_providers):
    client = get_gspread_client()
    sheet_id = st.secrets["SPREADSHEET_ID"]
    sh = client.open_by_key(sheet_id)
    
    ws_m = sh.worksheet("Monthly_Performance")
    ws_m.append_rows(df_monthly.values.tolist())
    
    ws_b = sh.worksheet("Benefits_Breakdown")
    ws_b.append_rows(df_benefits.values.tolist())
    
    ws_p = sh.worksheet("Top_Providers")
    ws_p.append_rows(df_providers.values.tolist())

def delete_session_data(target_session_id):
    client = get_gspread_client()
    sheet_id = st.secrets["SPREADSHEET_ID"]
    sh = client.open_by_key(sheet_id)
    
    worksheets = ["Monthly_Performance", "Benefits_Breakdown", "Top_Providers"]
    for ws_name in worksheets:
        ws = sh.worksheet(ws_name)
        session_col = ws.col_values(1)
        rows_to_delete = [i + 1 for i, val in enumerate(session_col) if val == target_session_id]
        for row_idx in reversed(rows_to_delete):
            ws.delete_rows(row_idx)

def calculate_month_weights(df_monthly, inception_date):
    if df_monthly.empty or inception_date is None:
        return df_monthly

    start_day = inception_date.day
    is_split_month = start_day > 1

    unique_months = sorted(df_monthly['month_code'].unique().tolist())
    total_months = len(unique_months)
    weight_map = {}

    for idx, m_code in enumerate(unique_months):
        year_val = int(m_code[:4])
        month_val = int(m_code[4:6])
        days_in_month = calendar.monthrange(year_val, month_val)[1]

        if not is_split_month:
            weight_map[m_code] = 1.0
        else:
            if idx == 0:
                coverage_days = days_in_month - start_day + 1
                weight_map[m_code] = round(coverage_days / days_in_month, 4)
            elif idx == 12 and total_months >= 13:
                coverage_days = start_day - 1
                weight_map[m_code] = round(coverage_days / days_in_month, 4)
            else:
                weight_map[m_code] = 1.0

    df_monthly['month_weight'] = df_monthly['month_code'].map(weight_map).fillna(1.0)
    return df_monthly

def parse_claims_report(uploaded_file, session_id, inception_date):
    fname = uploaded_file.name.lower()
    timestamp = datetime.utcnow().isoformat()
    monthly_rows = []
    benefits_rows = []
    providers_rows = []
    
    if fname.endswith(('.xlsx', '.xls')):
        xls = pd.ExcelFile(uploaded_file)
        
        if 'Monthly Claims' in xls.sheet_names:
            df_mc = pd.read_excel(xls, sheet_name='Monthly Claims', header=None)
            class_tier = str(df_mc.iloc[5, 1]) if pd.notna(df_mc.iloc[5, 1]) else "Class A"
            
            curr_year = None
            for _, row in df_mc.iterrows():
                val0 = str(row[0]).strip()
                if "2 Years Prior" in val0:
                    curr_year = "PY-1"
                    continue
                elif "Prior Policy Year" in val0:
                    curr_year = "PY"
                    continue
                elif "Last Policy Year" in val0 or "Current" in val0:
                    curr_year = "CY"
                    continue
                
                if re.match(r'^\d{6}$', val0) and curr_year:
                    c_count = clean_num(row[2])
                    if c_count is not None:
                        monthly_rows.append({
                            'session_id': session_id,
                            'class_tier': class_tier,
                            'policy_year': curr_year,
                            'month_code': val0,
                            'active_lives': clean_num(row[1]) or 0.0,
                            'claims_count': c_count,
                            'paid_claims_sar': clean_num(row[3]) or 0.0,
                            'paid_claims_vat_sar': clean_num(row[4]) or 0.0,
                            'created_at': timestamp
                        })
        
        if 'Breakdown by Benefit' in xls.sheet_names:
            df_bb = pd.read_excel(xls, sheet_name='Breakdown by Benefit', header=None)
            curr_year = None
            for _, row in df_bb.iterrows():
                val0 = str(row[0]).strip()
                if "2 Years Prior" in val0:
                    curr_year = "PY-1"
                    continue
                elif "Prior Policy Year" in val0:
                    curr_year = "PY"
                    continue
                elif "Last Policy Year" in val0 or "Current" in val0:
                    curr_year = "CY"
                    continue
                
                if curr_year and "Overall" not in val0 and "Monthly Claims" not in val0:
                    c_count = clean_num(row[1])
                    if c_count is not None:
                        benefits_rows.append({
                            'session_id': session_id,
                            'class_tier': class_tier,
                            'policy_year': curr_year,
                            'benefit_name': val0,
                            'claims_count': c_count,
                            'paid_claims_sar': clean_num(row[2]) or 0.0,
                            'paid_claims_vat_sar': clean_num(row[3]) or 0.0,
                            'created_at': timestamp
                        })

        if 'Top Providers' in xls.sheet_names:
            df_tp = pd.read_excel(xls, sheet_name='Top Providers', header=None)
            curr_year = None
            for _, row in df_tp.iterrows():
                val0 = str(row[0]).strip()
                if "2 Years Prior" in val0:
                    curr_year = "PY-1"
                    continue
                elif "Prior Policy Year" in val0:
                    curr_year = "PY"
                    continue
                elif "Lasr Policy Year" in val0 or "Last Policy Year" in val0:
                    curr_year = "CY"
                    continue
                
                if re.match(r'^\d+$', val0) and curr_year:
                    c_count = clean_num(row[2])
                    if c_count is not None:
                        providers_rows.append({
                            'session_id': session_id,
                            'class_tier': class_tier,
                            'policy_year': curr_year,
                            'rank': int(val0),
                            'provider_name': str(row[1]).strip(),
                            'claims_count': c_count,
                            'paid_claims_sar': clean_num(row[3]) or 0.0,
                            'paid_claims_vat_sar': clean_num(row[4]) or 0.0,
                            'created_at': timestamp
                        })

    elif fname.endswith('.pdf'):
        with pdfplumber.open(uploaded_file) as pdf:
            text = "\n".join([page.extract_text() or "" for page in pdf.pages])
            curr_year = "CY"
            for line in text.split("\n"):
                match = re.search(r'(\d{6})\s+(\d+)\s+(\d+)\s+([\d,\.]+)\s+([\d,\.]+)', line)
                if match:
                    monthly_rows.append({
                        'session_id': session_id,
                        'class_tier': "Class A",
                        'policy_year': curr_year,
                        'month_code': match.group(1),
                        'active_lives': clean_num(match.group(2)) or 0.0,
                        'claims_count': clean_num(match.group(3)) or 0.0,
                        'paid_claims_sar': clean_num(match.group(4)) or 0.0,
                        'paid_claims_vat_sar': clean_num(match.group(5)) or 0.0,
                        'created_at': timestamp
                    })

    df_monthly = pd.DataFrame(monthly_rows)
    df_benefits = pd.DataFrame(benefits_rows)
    df_providers = pd.DataFrame(providers_rows)

    if not df_monthly.empty:
        df_monthly = calculate_month_weights(df_monthly, inception_date)
        cols_order = [
            'session_id', 'class_tier', 'policy_year', 'month_code',
            'active_lives', 'claims_count', 'paid_claims_sar', 'paid_claims_vat_sar',
            'month_weight', 'created_at'
        ]
        df_monthly = df_monthly[cols_order]

    return df_monthly, df_benefits, df_providers

# --- واجهة المستخدم ---
st.markdown(f"### {t['title']}")
st.caption(t['subtitle'])

# 1. إدخال تاريخ بداية الوثيقة (بدون قيمة افتراضية لإلزام المستخدم)
inception_date = st.date_input(
    t['policy_date_label'],
    value=None
)

# 2. المدخلات المتقدمة الاختيارية مع تنسيق الفواصل الألفية
with st.expander(t['expander_label']):
    current_premium = st.number_input(
        t['premium_label'],
        min_value=0.0,
        value=0.0,
        step=50000.0,
        format="%f"  # يتيح عرض الفواصل والكسور بدقة
    )
    if current_premium > 0:
        st.caption(f"القيمة المدخلة: **{current_premium:,.2f}** SAR")

    target_census = st.number_input(
        t['census_label'],
        min_value=0,
        value=0,
        step=10,
        format="%d"
    )
    if target_census > 0:
        st.caption(f"التعداد المحدد: **{target_census:,}** فرد / مكفول")

# 3. رافع الملفات
uploaded = st.file_uploader(t['uploader_label'], type=["xlsx", "xls", "pdf"])

if 'active_session' not in st.session_state:
    st.session_state.active_session = None

if uploaded and not st.session_state.active_session:
    if inception_date is None:
        st.error(t['date_warning'])
    else:
        session_id = generate_session_id()
        with st.spinner(t['processing']):
            df_monthly, df_benefits, df_providers = parse_claims_report(uploaded, session_id, inception_date)
            
            if df_monthly.empty:
                st.error(t['error_parse'])
            else:
                try:
                    append_to_sheets(df_monthly, df_benefits, df_providers)
                    st.session_state.active_session = session_id
                    st.session_state.current_premium = current_premium
                    st.session_state.target_census = target_census
                    
                    excel_buffer = io.BytesIO()
                    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                        df_monthly.to_excel(writer, sheet_name='Monthly_Performance', index=False)
                        df_benefits.to_excel(writer, sheet_name='Benefits_Breakdown', index=False)
                        df_providers.to_excel(writer, sheet_name='Top_Providers', index=False)
                    st.session_state.backup_data = excel_buffer.getvalue()
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"{t['error_api']} ({str(e)})")

if st.session_state.active_session:
    sid = st.session_state.active_session
    c_prem = st.session_state.get('current_premium', 0.0)
    t_cens = st.session_state.get('target_census', 0)
    
    params = {
        "ds0.p_session_id": sid,
        "ds1.p_session_id": sid,
        "ds2.p_session_id": sid
    }
    if c_prem > 0:
        params["ds0.p_current_premium"] = float(c_prem)
    if t_cens > 0:
        params["ds0.p_target_census"] = int(t_cens)

    encoded_params = urllib.parse.quote(str(params).replace("'", '"'))
    looker_url = f"{LOOKER_STUDIO_BASE_URL}?params={encoded_params}"
    
    st.success(f"{t['success_msg']} **{sid}**")
    
    col1, col2 = st.columns(2)
    with col1:
        st.link_button(t['btn_dashboard'], looker_url, type="primary")
    with col2:
        if 'backup_data' in st.session_state:
            st.download_button(
                label=t['btn_download'],
                data=st.session_state.backup_data,
                file_name=f"Clean_Claims_{sid}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
    
    st.divider()
    
    if st.button(t['btn_purge'], type="secondary"):
        with st.spinner(t['purging_msg']):
            delete_session_data(sid)
            st.session_state.active_session = None
            if 'backup_data' in st.session_state:
                del st.session_state.backup_data
            st.success(t['purge_success'])
            st.rerun()
