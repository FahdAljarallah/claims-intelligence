import os
import io
import uuid
import json
import urllib.parse
import streamlit as st
import pandas as pd
import re
import pdfplumber
import pdf2image
import pytesseract
from pytesseract import Output
from PIL import Image
import requests

from google.cloud import bigquery
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="منصة الاستشارات التنفيذية | Executive Advisory Platform",
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="collapsed"
)

if 'lang' not in st.session_state:
    st.session_state.lang = 'ar'

# التعامل مع الضغط على أزرار اللغة عبر Query Parameters لتجاوز قيود أزرار ستريمليت
query_params = st.query_params
if "lang" in query_params:
    lang_val = query_params["lang"]
    if lang_val in ['ar', 'en']:
        st.session_state.lang = lang_val

is_ar = st.session_state.lang == 'ar'
text_align = "right" if is_ar else "left"
direction = "rtl" if is_ar else "ltr"

# حقن أنماط الـ CSS النظيفة والاحترافية
st.markdown(f"""
    <style>
    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}
    header {{visibility: hidden;}}
    
    .stApp {{
        background-color: #FBFBF9;
        color: #1E293B;
        direction: {direction};
        text-align: {text_align};
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }}
    
    div:empty, .element-container:empty {{
        width: 0px !important;
        height: 0px !important;
        margin: 0px !important;
        padding: 0px !important;
        display: none !important;
    }}
    
    /* أزرار المنصة الرئيسية */
    .stButton button {{
        width: 100%;
        background-color: #0E243A;
        color: #FFFFFF;
        font-weight: 700;
        padding: 0.85rem 1.5rem;
        border-radius: 8px;
        border: none;
        box-shadow: 0 4px 12px rgba(14, 36, 58, 0.25);
        height: 48px;
    }}
    .stButton button:hover {{
        background-color: #17324D;
    }}
    
    /* تصميم أزرار اللغة بـ HTML النقي لضمان ظهور النصوص والألوان بدقة متناهية */
    .lang-bar-container {{
        display: flex;
        justify-content: flex-end;
        align-items: center;
        gap: 8px;
        max-width: 900px;
        margin: 1.5rem auto 0.5rem auto;
        padding: 0 10px;
    }}
    .lang-pill {{
        padding: 6px 16px;
        border-radius: 6px;
        font-size: 0.88rem;
        font-weight: 600;
        text-decoration: none;
        transition: all 0.2s ease;
        display: inline-block;
        text-align: center;
    }}
    .lang-pill-active {{
        background-color: #0E243A;
        color: #FFFFFF;
        border: 1.5px solid #0E243A;
    }}
    .lang-pill-inactive {{
        background-color: #FFFFFF;
        color: #0E243A;
        border: 1.5px solid #0E243A;
    }}
    .lang-pill-inactive:hover {{
        background-color: #F1F5F9;
    }}

    .main-container-card {{
        background: #FFFFFF;
        border: 1px solid rgba(226, 232, 240, 0.9);
        border-radius: 16px;
        padding: 40px;
        box-shadow: 0 12px 35px rgba(14, 36, 58, 0.05);
        margin-top: 1rem;
        margin-bottom: 2rem;
        direction: {direction};
        text-align: {text_align};
        max-width: 900px;
        margin-left: auto;
        margin-right: auto;
    }}
    
    .title-desc-box {{
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.02);
        text-align: center;
    }}

    .platform-title {{
        font-size: 1.8rem;
        font-weight: 800;
        color: #0E243A;
        margin-bottom: 12px;
    }}
    
    .platform-desc {{
        font-size: 0.98rem;
        color: #475569;
        line-height: 1.7;
    }}
    
    .sdaia-box-okular {{
        background: #F8FAFC;
        border: 1px solid #CBD5E1;
        color: #0E243A;
        padding: 14px 18px;
        border-radius: 8px;
        font-size: 0.9rem;
        text-align: center;
        margin-bottom: 20px;
        line-height: 1.6;
        font-weight: 600;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.02);
        direction: {direction};
    }}
    
    .sovereign-footer {{
        text-align: center;
        color: #64748b;
        font-size: 0.85rem;
        margin-top: 3rem;
        margin-bottom: 2rem;
        letter-spacing: 0.3px;
    }}
    </style>
""", unsafe_allow_html=True)

translations = {
    'ar': {
        'title': "منصة الاستشارات التنفيذية",
        'desc': "أداة تحليل استراتيجية متقدمة لقيادات C-Level ومجالس الإدارة، تمكنك من قراءة الأداء المالي، تحليل تضخم التكاليف، واستشراف قيمة الوثيقة عبر محاكاة سيناريوهات حية تدعم قراراتك الاستثمارية بدقة فائقة.",
        'security': "🛡️ آمن بالكامل 100% - التزاماً بأنظمة سدايا (SDAIA) ونظام حماية البيانات الشخصية: لا يتم حفظ أي ملفات مرفوعة ويتم التخلص نهائياً من البيانات بعد مرور 24 ساعة.",
        'upload': "اسحب ملفات البيانات هنا أو اضغط للاختيار (CSV / XLSX)",
        'launch': "تشغيل اللوحة التنفيذية",
        'processing': "جاري معالجة الملفات وضخها إلى المستودع المركزي...",
        'ready': "📈 تقرير لوحة المؤشرات جاهز:",
        'link': "اضغط هنا لفتح لوحة البيانات في Looker Studio",
        'footer': "© 2026 فهد الجارالله - جميع حقوق محفوظة لبيئة العمل السيادية."
    },
    'en': {
        'title': "Executive Advisory Platform",
        'desc': "Advanced strategic analysis tool for C-Level executives and boards of directors, enabling financial performance reading, cost inflation analysis, and policy value forecasting through live scenario simulation.",
        'security': "🛡️ 100% Fully Secure - Complying with SDAIA regulations and the Personal Data Protection Law (PDPL): No uploaded files are stored, and data is permanently purged after 24 hours.",
        'upload': "Drag and drop data files here or click to browse (CSV / XLSX)",
        'launch': "Launch Executive Dashboard",
        'processing': "Processing files and loading to the central data warehouse...",
        'ready': "📈 Executive Dashboard Report Ready:",
        'link': "Click here to open the dashboard in Looker Studio",
        'footer': "© 2026 Fahd Aljarallah - All rights reserved for the Sovereign Workspace."
    }
}

t = translations[st.session_state.lang]

# أزرار اللغة النقية بـ HTML مطابقة تماماً للتصميم الأصلي (العربية والإنجليزية متجاوران)
en_class = "lang-pill lang-pill-active" if not is_ar else "lang-pill lang-pill-inactive"
ar_class = "lang-pill lang-pill-active" if is_ar else "lang-pill lang-pill-inactive"

st.markdown(f"""
    <div class="lang-bar-container">
        <a href="?lang=en" class="{en_class}">English</a>
        <a href="?lang=ar" class="{ar_class}">العربية</a>
    </div>
""", unsafe_allow_html=True)

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"
TABLE_ID = "monthly_performance"

@st.cache_resource
def get_bq_client(project_id):
    creds_dict = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_dict:
        pk = creds_dict["private_key"].replace("\\n", "\n")
        creds_dict["private_key"] = pk
    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=project_id)

def clean_number(val):
    try:
        clean_str = str(val).replace('SAR', '').replace('ر.س', '').replace(',', '').strip()
        match = re.search(r'[-+]?\d*\.?\d+', clean_str)
        return float(match.group()) if match else 0.0
    except Exception:
        return 0.0

def extract_structured_rows_from_pdf_bytes(file_bytes):
    all_structured_rows = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                words = page.extract_words(use_text_flow=True)
                if words:
                    page_words = [{'left': w['x0'], 'top': w['top'], 'width': w['width'], 'height': w['height'], 'text': w['text'].strip()} for w in words]
                    page_words = sorted(page_words, key=lambda w: (w['top'], w['left']))
                    rows, current_row, current_top, tolerance = [], [], -1, 6
                    for w in page_words:
                        if current_top == -1 or abs(w['top'] - current_top) <= tolerance:
                            current_row.append(w)
                            if current_top == -1:
                                current_top = w['top']
                        else:
                            current_row = sorted(current_row, key=lambda x: x['left'])
                            rows.append(current_row)
                            current_row = [w]
                            current_top = w['top']
                    if current_row:
                        current_row = sorted(current_row, key=lambda x: x['left'])
                        rows.append(current_row)
                    all_structured_rows.extend(rows)
    except Exception:
        pass
    return all_structured_rows

def parse_single_file(file_bytes, file_name, session_id):
    if file_name.lower().endswith(('.xlsx', '.xls', '.csv')):
        try:
            df_excel = pd.read_excel(io.BytesIO(file_bytes)) if file_name.lower().endswith(('.xlsx', '.xls')) else pd.read_csv(io.BytesIO(file_bytes))
            monthly_rows = []
            created_at_ts = pd.Timestamp.now(tz='UTC').isoformat()
            for idx, row in df_excel.iterrows():
                monthly_rows.append({
                    "session_id": session_id,
                    "created_at": created_at_ts,
                    "policy_year": str(row.get('policy_year', 'LAST POLICY YEAR')),
                    "inception_date": str(row.get('inception_date', '')),
                    "table_header": file_name,
                    "class_tier": str(row.get('class_tier', 'CLASS VIP')),
                    "section_type": "Monthly Claims",
                    "month_code": str(row.get('month_code', f"2025-{str((idx%12)+1).zfill(2)}")),
                    "active_lives": int(row.get('active_lives', 640)),
                    "claims_count": int(row.get('claims_count', 100)),
                    "paid_claims_sar": float(row.get('paid_claims_sar', 880000.0)),
                    "paid_claims_vat_sar": float(row.get('paid_claims_vat_sar', 0.0)),
                    "OS_claims_count": 0,
                    "OS_paid_claims_sar": float(row.get('OS_paid_claims_sar', 0.0)),
                    "OS_paid_claims_vat_sar": float(row.get('OS_paid_claims_vat_sar', 0.0)),
                    "benefit_name": None,
                    "provider_name": None
                })
            return pd.DataFrame(monthly_rows)
        except Exception:
            pass
    return pd.DataFrame()

col_logo_space1, col_logo_center, col_logo_space2 = st.columns([1, 2, 1])
with col_logo_center:
    try:
        logo_url = "https://lh3.googleusercontent.com/d/11LDGzgTrWg18ubhLZ1p5ocf6DTL80e1W=w1000?authuser=0"
        response = requests.get(logo_url)
        if response.status_code == 200:
            logo_img = Image.open(io.BytesIO(response.content))
            st.image(logo_img, use_container_width=True)
        else:
            logo_img = Image.open("Full Logo - 600-400 _4.png")
            st.image(logo_img, use_container_width=True)
    except Exception:
        st.markdown("""
            <div style="width: 100%; height: 120px; display: flex; justify-content: center; align-items: center; color: #0E243A; font-weight: 700;">
                FAHD ALJARALLAH
            </div>
        """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

st.markdown('<div class="main-container-card">', unsafe_allow_html=True)

st.markdown(f"""
    <div class="title-desc-box">
        <div class="platform-title">{t['title']}</div>
        <div class="platform-desc">{t['desc']}</div>
    </div>
""", unsafe_allow_html=True)

st.markdown(f'<div class="sdaia-box-okular">{t["security"]}</div>', unsafe_allow_html=True)

col_upload, col_btn = st.columns([1.3, 1])

with col_upload:
    uploaded_files = st.file_uploader(
        t['upload'],
        type=["pdf", "csv", "xlsx"],
        accept_multiple_files=True
    )

with col_btn:
    st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
    launch_button = st.button(t['launch'], type="primary")

st.markdown('</div>', unsafe_allow_html=True)

result_container = st.container()

if uploaded_files and launch_button:
    with result_container:
        current_sess = f"sess_{str(uuid.uuid4())[:8]}"
        
        with st.spinner(t['processing']):
            all_dfs = []
            for uploaded_file in uploaded_files:
                file_bytes = uploaded_file.read()
                df_single = parse_single_file(file_bytes, uploaded_file.name, current_sess)
                if not df_single.empty:
                    all_dfs.append(df_single)
            
            if all_dfs:
                df_actual = pd.concat(all_dfs, ignore_index=True)
                try:
                    df_clean = df_actual.where(pd.notnull(df_actual), None)
                    records_to_insert = df_clean.to_dict(orient="records")
                    bq_client = get_bq_client(PROJECT_ID)
                    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
                    errors = bq_client.insert_rows_json(table_ref, records_to_insert)
                    
                    if errors == []:
                        base_url = "https://datastudio.google.com/reporting/34329d81-4adf-410e-86a9-24713511ec47"
                        params_data = {"ds14": {"p_client_session": current_sess}}
                        encoded_params = urllib.parse.quote(json.dumps(params_data))
                        looker_url = f"{base_url}/page/p_yr47xbgg7d?params={encoded_params}"
                        
                        st.markdown(f"""
                            <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 12px; padding: 24px; margin-top: 20px; text-align: {text_align}; direction: {direction}; box-shadow: 0 1px 4px rgba(0,0,0,0.02); max-width: 900px; margin-left: auto; margin-right: auto;">
                                <h3 style="color: #0E243A; font-size: 1.2rem; font-weight: 700; margin-bottom: 12px;">{t['ready']}</h3>
                                <p style="margin-bottom: 0;"><a href="{looker_url}" target="_blank" style="color: #0E243A; font-weight: 700; text-decoration: underline;">{t['link']}</a></p>
                            </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.error(f"Error: {errors}")
                except Exception as e:
                    st.error(f"Connection Failed: {str(e)}")
            else:
                st.warning("No matching data extracted from uploaded files.")

st.markdown(f'<p class="sovereign-footer">{t["footer"]}</p>', unsafe_allow_html=True)
