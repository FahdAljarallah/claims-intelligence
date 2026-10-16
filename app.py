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

from google.cloud import bigquery
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="منصة الاستشارات التنفيذية | Executive Advisory Platform",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    .stApp {
        background: radial-gradient(circle at 50% 20%, #111827 0%, #030712 100%);
        color: #f3f4f6;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }
    
    .landing-card {
        background: rgba(17, 24, 39, 0.75);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 16px;
        padding: 40px;
        box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
        backdrop-filter: blur(12px);
        margin-top: 1rem;
    }
    
    .platform-title {
        font-size: 2.2rem;
        font-weight: 800;
        color: #ffffff;
        text-align: center;
        margin-bottom: 0.5rem;
        letter-spacing: -0.5px;
    }
    
    .platform-desc {
        font-size: 1.05rem;
        color: #9ca3af;
        text-align: center;
        line-height: 1.6;
        margin-bottom: 2rem;
    }
    
    .sdaia-badge {
        background: rgba(16, 185, 129, 0.1);
        border: 1px solid rgba(16, 185, 129, 0.2);
        color: #34d399;
        padding: 12px 16px;
        border-radius: 8px;
        font-size: 0.9rem;
        text-align: center;
        margin-bottom: 2rem;
    }
    
    .stButton button {
        width: 100%;
        background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
        color: white;
        font-weight: 600;
        padding: 0.75rem 1.5rem;
        border-radius: 8px;
        border: none;
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
        transition: all 0.3s ease;
    }
    .stButton button:hover {
        background: linear-gradient(135deg, #2563eb 0%, #1e40af 100%);
        box-shadow: 0 6px 16px rgba(59, 130, 246, 0.5);
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
                "section_type": "Monthly Claims",
                "month_code": "Number of lives at start",
                "active_lives": lives_val,
                "claims_count": 0,
                "paid_claims_sar": 0.0,
                "paid_claims_vat_sar": 0.0,
                "OS_claims_count": 0,
                "OS_paid_claims_sar": 0.0,
                "OS_paid_claims_vat_sar": 0.0,
                "benefit_name": None,
                "provider_name": None
            })
            continue

        date_match = re.search(r'\b(20\d{2})(0[1-9]|1[0-2])\b|\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b|\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b', line_lower)
        is_tawuniya_format = bool(row_tokens and re.match(r'^(0?[1-9]|1[0-2])[\/\-](20\d{2})$', row_tokens[0]))

        if date_match or is_tawuniya_format:
            if is_tawuniya_format:
                parts = row_tokens[0].replace('-', '/').split('/')
                row_date = f"{int(parts[1])}-{str(int(parts[0])).zfill(2)}"
            elif date_match:
                if date_match.group(1) and date_match.group(2):
                    row_date = f"{date_match.group(1)}-{str(int(date_match.group(2))).zfill(2)}"
                elif date_match.group(3) and date_match.group(4):
                    row_date = f"{date_match.group(4)}-{str(int(date_match.group(3))).zfill(2)}"
                elif date_match.group(5) and date_match.group(6):
                    row_date = f"{date_match.group(5)}-{str(int(date_match.group(6))).zfill(2)}"
                else:
                    row_date = row_text[:15].strip()
            else:
                row_date = row_text[:15].strip()
            
            month_key = (file_name, current_class_tier, row_date)
            if month_key in seen_months:
                continue
            seen_months.add(month_key)
            
            filtered_tokens = []
            for idx, t in enumerate(row_tokens):
                if idx == 0 and (t in ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'] or '/' in t or '-' in t):
                    continue
                if re.search(r'\b20\d{2}(0[1-9]|1[0-2])\b|\b(0?[1-9]|1[0-2])[\/\-]20\d{2}\b|\b20\d{2}[\/\-](0?[1-9]|1[0-2])\b', t):
                    continue
                filtered_tokens.append(t)
            
            nums = [clean_number(t) for t in filtered_tokens if re.search(r'\d', t)]
            
            if nums:
                monthly_rows.append({
                    "session_id": session_id,
                    "created_at": created_at_ts,
                    "policy_year": current_policy_year or "LAST POLICY YEAR",
                    "inception_date": current_inception or "",
                    "table_header": file_name,
                    "class_tier": current_class_tier,
                    "section_type": "Monthly Claims",
                    "month_code": row_date,
                    "active_lives": int(nums[0]) if len
