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

# حقن أنماط الـ CSS المتقدمة وضبط محاذاة الصناديق والنتائج باليمين
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    .stApp {
        background-color: #FBFBF9;
        color: #1E293B;
        direction: rtl;
        text-align: right;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    
    div:empty, .element-container:empty {
        width: 0px !important;
        height: 0px !important;
        margin: 0px !important;
        padding: 0px !important;
        display: none !important;
    }
    
    /* البطاقة المركزية الكبرى */
    .main-container-card {
        background: #FFFFFF;
        border: 1px solid rgba(226, 232, 240, 0.9);
        border-radius: 16px;
        padding: 40px;
        box-shadow: 0 12px 35px rgba(14, 36, 58, 0.05);
        margin-top: 1rem;
        margin-bottom: 2rem;
        direction: rtl;
        text-align: right;
        max-width: 900px;
        margin-left: auto;
        margin-right: auto;
    }
    
    /* صندوق العنوان والشرح المنفصل */
    .title-desc-box {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.02);
        text-align: center;
    }

    .platform-title {
        font-size: 1.8rem;
        font-weight: 800;
        color: #0E243A;
        margin-bottom: 12px;
    }
    
    .platform-desc {
        font-size: 0.98rem;
        color: #475569;
        line-height: 1.7;
    }
    
    /* صندوق سدايا الأمني المطابق تماماً في الشكل واللون */
    .sdaia-box-okular {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        color: #065F46;
        padding: 14px 18px;
        border-radius: 8px;
        font-size: 0.9rem;
        text-align: center;
        margin-bottom: 20px;
        line-height: 1.6;
        font-weight: 600;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.02);
    }
    
    .stButton button {
        width: 100%;
        background-color: #0E243A;
        color: #FFFFFF;
        font-weight: 700;
        padding: 0.85rem 1.5rem;
        border-radius: 8px;
        border: none;
        box-shadow: 0 4px 12px rgba(14, 36, 58, 0.25);
        height: 48px;
    }
    .stButton button:hover {
        background-color: #17324D;
    }
    
    .sovereign-footer {
        text-align: center;
        color: #64748b;
        font-size: 0.85rem;
        margin-top: 3rem;
        margin-bottom: 2rem;
        letter-spacing: 0.3px;
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

    if not all_structured_rows:
        try:
            images = pdf2image.convert_from_bytes(file_bytes)
            for img in images:
                data = pytesseract.image_to_data(img, output_type=Output.DICT, lang='eng+ara')
                words = [{'left': data['left'][i], 'top': data['top'][i], 'width': data['width'][i], 'height': data['height'][i], 'text': data['text'][i].strip()} for i in range(len(data['text'])) if data['text'][i].strip()]
                words = sorted(words, key=lambda w: (w['top'], w['left']))
                rows, current_row, current_top, tolerance = [], [], -1, 12
                for w in words:
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
        except Exception as e:
            st.error(f"خطأ في معالجة ملف الـ PDF: {str(e)}")
            
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
                    "OS_claims_count": int(row.get('OS_claims_count', 0)),
                    "OS_paid_claims_sar": float(row.get('OS_paid_claims_sar', 0.0)),
                    "OS_paid_claims_vat_sar": float(row.get('OS_paid_claims_vat_sar', 0.0)),
                    "benefit_name": None,
                    "provider_name": None
                })
            return pd.DataFrame(monthly_rows)
        except Exception:
            pass

    monthly_rows = []
    all_structured_rows = extract_structured_rows_from_pdf_bytes(file_bytes)
    if not all_structured_rows:
        return pd.DataFrame()

    current_policy_year = ""
    current_inception = ""
    current_class_tier = "CLASS VIP"
    seen_months = set()
    
    for row in all_structured_rows:
        row_tokens = [w['text'] for w in row]
        row_text = " ".join(row_tokens)
        line_lower = row_text.lower()
        
        if "confidential" in line_lower:
            continue
            
        if "policy year" in line_lower or "last policy year" in line_lower or "سنة الوثيقة" in line_lower or "period" in line_lower:
            current_policy_year = row_text.strip()
            continue
            
        if "inception" in line_lower:
            date_m = re.search(r'\b(0?[1-9]|[12][0-9]|3[01])[-/](0?[1-9]|1[0-2])[-/](20\d{2})\b', row_text)
            if date_m:
                current_inception = date_m.group(0)
            continue
            
        if "class" in line_lower or "tier" in line_lower or "الفئة" in line_lower or "product" in line_lower:
            clean_class = re.sub(r'(class\s*type|class\s*tier|product\s*/\s*class|الفئة[:\s]*)', '', row_text, flags=re.IGNORECASE).strip()
            if not clean_class and ":" in row_text:
                clean_class = row_text.split(":")[-1].strip()
            clean_class = re.split(r'\b(dental|inpatient|outpatient|optical|maternity|limit|deductible)\b', clean_class, flags=re.IGNORECASE)[0].strip()
            if clean_class and "confidential" not in clean_class.lower():
                clean_class = re.sub(r'[.:\-]$', '', clean_class).strip()
                if clean_class and len(clean_class) < 20 and not re.search(r'[\u0600-\u06FF]', clean_class):
                    current_class_tier = clean_class
            continue

        created_at_ts = pd.Timestamp.now(tz='UTC').isoformat()
        
        if "number of lives at start" in line_lower or "lives at start" in line_lower:
            nums_lives = [clean_number(t) for t in row_tokens if re.search(r'\d', t)]
            lives_val = int(nums_lives[0]) if nums_lives else 100
            monthly_rows.append({
                "session_id": session_id,
                "created_at": created_at_ts,
                "policy_year": current_policy_year or "LAST POLICY YEAR",
                "inception_date": current_inception or "",
                "table_header": file_name,
                "class_tier": current_class_tier,
                "section
