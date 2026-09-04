import io
import re
import uuid
import urllib.parse
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
        "subtitle": "الأنظمة المدعومة: تقارير هيئة التأمين بصيغة Excel أو PDF. معالجة معزولة ومحمية بالكامل.",
        "uploader_label": "اختر ملف تقرير المطالبات للبدء بالتحليل اللحظي:",
        "processing": "جارٍ استخراج البيانات، عزل الجلسة، وتغذية لوحة التفاوض...",
        "error_parse": "تعذر التعرف على جداول التقرير. يرجى التأكد من رفع التقرير المعتمد من الهيئة.",
        "error_api": "حدث خطأ أثناء تحديث قاعدة البيانات المركزية. يرجى مراجعة الصلاحيات.",
        "success_msg": "تمت معالجة البيانات وحقنها بنجاح! رمز جلستك المعزولة:",
        "btn_dashboard": "🚀 فتح لوحة التحليل والتفاوض الخاصة بك",
        "btn_download": "📥 تحميل نسخة احتياطية مسطحة (Excel)",
        "sec_lang": "اللغة / Language"
    },
    "en": {
        "title": "📤 Claims Experience Ingestor & Intelligence",
        "subtitle": "Supported formats: Insurance Authority standardized Excel or PDF reports. Fully session-isolated.",
        "uploader_label": "Upload Claims Experience report to start real-time analysis:",
        "processing": "Standardizing data, isolating negotiation session, and updating analytical layer...",
        "error_parse": "Failed to parse standardized tables. Please ensure the official Insurance Authority format is uploaded.",
        "error_api": "Error occurred while updating the central repository. Please verify access permissions.",
        "success_msg": "Data processed and integrated successfully! Isolated Session ID:",
        "btn_dashboard": "🚀 Launch Negotiation & Analysis Dashboard",
        "btn_download": "📥 Download Clean Backup Data (Excel)",
        "sec_lang": "Language / اللغة"
    }
}

# اختيار اللغة
with st.sidebar:
    lang_choice = st.radio(
        "Interface Language / لغة الواجهة",
        options=["العربية", "English"],
        index=0
    )
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
    
    # 1. Monthly_Performance
    ws_m = sh.worksheet("Monthly_Performance")
    ws_m.append_rows(df_monthly.values.tolist())
    
    # 2. Benefits_Breakdown
    ws_b = sh.worksheet("Benefits_Breakdown")
    ws_b.append_rows(df_benefits.values.tolist())
    
    # 3. Top_Providers
    ws_p = sh.worksheet("Top_Providers")
    ws_p.append_rows(df_providers.values.tolist())

def parse_claims_report(uploaded_file, session_id):
    fname = uploaded_file.name.lower()
    monthly_rows = []
    benefits_rows = []
    providers_rows = []
    
    if fname.endswith(('.xlsx', '.xls')):
        xls = pd.ExcelFile(uploaded_file)
        
        # 1. Monthly Claims
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
                            'paid_claims_vat_sar': clean_num(row[4]) or 0.0
                        })
        
        # 2. Breakdown by Benefit
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
                            'paid_claims_vat_sar': clean_num(row[3]) or 0.0
                        })

        # 3. Top Providers
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
                            'paid_claims_vat_sar': clean_num(row[4]) or 0.0
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
                        'paid_claims_vat_sar': clean_num(match.group(5)) or 0.0
                    })

    return (
        pd.DataFrame(monthly_rows),
        pd.DataFrame(benefits_rows),
        pd.DataFrame(providers_rows)
    )

st.markdown(f"### {t['title']}")
st.caption(t['subtitle'])

uploaded = st.file_uploader(t['uploader_label'], type=["xlsx", "xls", "pdf"])

if uploaded:
    session_id = generate_session_id()
    with st.spinner(t['processing']):
        df_monthly, df_benefits, df_providers = parse_claims_report(uploaded, session_id)
        
        if df_monthly.empty:
            st.error(t['error_parse'])
        else:
            try:
                # الحقن البرمجي المباشر في Google Sheets
                append_to_sheets(df_monthly, df_benefits, df_providers)
                
               params = {
            "ds0.p_session_id": session_id,
            "ds1.p_session_id": session_id,
            "ds2.p_session_id": session_id
        }
                encoded_params = urllib.parse.quote(str(params).replace("'", '"'))
                looker_url = f"{LOOKER_STUDIO_BASE_URL}?params={encoded_params}"
                
                st.success(f"{t['success_msg']} **{session_id}**")
                
                st.link_button(t['btn_dashboard'], looker_url, type="primary")
                
                # خيار حفظ نسخة مسطحة كاحتياط
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                    df_monthly.to_excel(writer, sheet_name='Monthly_Performance', index=False)
                    df_benefits.to_excel(writer, sheet_name='Benefits_Breakdown', index=False)
                    df_providers.to_excel(writer, sheet_name='Top_Providers', index=False)
                
                st.download_button(
                    label=t['btn_download'],
                    data=excel_buffer.getvalue(),
                    file_name=f"Clean_Claims_{session_id}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except Exception as e:
                st.error(f"{t['error_api']} ({str(e)})")
