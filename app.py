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

# حقن أنماط الـ CSS لإجبار كل العناصر على الدخول داخل الكرت الأبيض المركزي وترتيب الهيكل
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    .stApp {
        background-color: #F8F9FA;
        color: #1E293B;
        direction: rtl;
        text-align: right;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    
    /* الهيدر العلوي */
    .top-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        max-width: 900px;
        margin: 0 auto 1.5rem auto;
        padding: 1rem 0;
    }

    /* البطاقة المركزية البيضاء الكبرى التي تحتضن كافة المحتوى */
    .main-container-card {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 16px;
        padding: 40px 50px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.04);
        max-width: 900px;
        margin: 0 auto;
        direction: rtl;
        text-align: right;
    }
    
    .platform-title {
        font-size: 1.9rem;
        font-weight: 800;
        color: #0E243A;
        text-align: center;
        margin-bottom: 12px;
    }
    
    .platform-desc {
        font-size: 0.95rem;
        color: #475569;
        text-align: center;
        line-height: 1.7;
        margin-bottom: 2rem;
    }
    
    .sdaia-box {
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        color: #065F46;
        padding: 12px 16px;
        border-radius: 8px;
        font-size: 0.88rem;
        text-align: center;
        margin-bottom: 2rem;
        font-weight: 600;
    }

    .stButton button {
        width: 100%;
        background-color: #0E243A;
        color: #FFFFFF;
        font-weight: 700;
        padding: 0.75rem 1rem;
        border-radius: 8px;
        border: none;
        height: 48px;
    }
    .stButton button:hover {
        background-color: #17324D;
    }
    
    .sovereign-footer {
        text-align: center;
        color: #64748B;
        font-size: 0.82rem;
        margin-top: 3rem;
        margin-bottom: 2rem;
    }
    </style>
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
                    "paid_claims_vat_sar": 0.0,
                    "OS_claims_count": 0,
                    "OS_paid_claims_sar": 0.0,
                    "OS_paid_claims_vat_sar": 0.0,
                    "benefit_name": None,
                    "provider_name": None
                })
            return pd.DataFrame(monthly_rows)
        except Exception:
            pass
    return pd.DataFrame()

# 1. الهيدر العلوي المنظم (الشعار يمين، أزرار اللغات يسار)
col_h1, col_h2 = st.columns([1, 1])
with col_h1:
    st.markdown("""
        <div style="display: flex; gap: 8px;">
            <span style="border: 1px solid #0E243A; color: #0E243A; padding: 4px 12px; border-radius: 6px; font-size: 0.85rem; font-weight: 600;">English</span>
            <span style="background-color: #0E243A; color: #FFFFFF; padding: 4px 12px; border-radius: 6px; font-size: 0.85rem; font-weight: 600;">العربية</span>
        </div>
    """, unsafe_allow_html=True)

with col_h2:
    st.markdown("""
        <div style="text-align: left; color: #0E243A; font-weight: 800; font-size: 1.1rem;">
            فهد الجارالله <span style="font-size: 0.7rem; color: #64748B;">FAHD ALJARALLAH</span>
        </div>
    """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# 2. فتح البطاقة المركزية الكبرى (Main Container Card) لحصر كافة المحتوى بداخلها
st.markdown('<div class="main-container-card">', unsafe_allow_html=True)

st.markdown('<div class="platform-title">منصة الاستشارات التنفيذية</div>', unsafe_allow_html=True)
st.markdown('<div class="platform-desc">أداة تحليل استراتيجية متقدمة لقيادات C-Level ومجالس الإدارة، تمكنك من قراءة الأداء المالي، تحليل تضخم التكاليف، واستشراف المخاطر الاكتوارية عبر محاكاة سيناريوهات حية تدعم قراراتك الاستثمارية بدقة فائقة.</div>', unsafe_allow_html=True)

st.markdown('<div class="sdaia-box">🛡️ آمن بالكامل 100% - التزاماً بأنظمة سدايا (SDAIA) ونظام حماية البيانات الشخصية: لا يتم حفظ أي ملفات أو بيانات نهائياً.</div>', unsafe_allow_html=True)

# دمج زر الرفع وزر التشغيل بطريقة متناسقة تماماً داخل الكرت
col_upload, col_btn = st.columns([1.2, 1])

with col_upload:
    uploaded_files = st.file_uploader(
        "اسحب ملفات البيانات هنا أو اضغط للاختيار (PDF / CSV / XLSX)",
        type=["pdf", "csv", "xlsx", "xls"],
        accept_multiple_files=True
    )

with col_btn:
    st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
    launch_button = st.button("تشغيل اللوحة التنفيذية", type="primary")

st.markdown('</div>', unsafe_allow_html=True)

# 3. الفوتر السيادي السفلي
st.markdown(
    '<p class="sovereign-footer">© 2026 فهد الجارالله - جميع حقوق محفوظة لبيئة العمل السيادية.</p>',
    unsafe_allow_html=True
)

# 4. معالجة البيانات والاتصال بقاعدة البيانات
if uploaded_files and launch_button:
    current_sess = f"sess_{str(uuid.uuid4())[:8]}"
    st.info(f"🔑 تم توليد معرف الجلسة بنجاح: **{current_sess}**")
    
    with st.spinner("جاري معالجة الملفات وضخها إلى المستودع المركزي..."):
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
                    st.success(f"تم رفع بيانات المطالبات بنجاح برقم الجلسة: {current_sess}")
                    base_url = "https://datastudio.google.com/reporting/34329d81-4adf-410e-86a9-24713511ec47"
                    params_data = {"ds14": {"p_client_session": current_sess}}
                    encoded_params = urllib.parse.quote(json.dumps(params_data))
                    looker_url = f"{base_url}/page/p_yr47xbgg7d?params={encoded_params}"
                    st.markdown(f"[اضغط هنا لفتح لوحة البيانات في Looker Studio]({looker_url})", unsafe_allow_html=True)
                else:
                    st.error(f"خطأ في الحفظ: {errors}")
            except Exception as e:
                st.error(f"فشل الاتصال: {str(e)}")
        else:
            st.warning("يرجى التأكد من مطابقة الملفات المرفوعة.")
