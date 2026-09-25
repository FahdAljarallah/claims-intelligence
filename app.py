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

st.set_page_config(page_title="مرصد المطالبات التأمينية الذكي", page_icon="📊", layout="wide")

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
                    page_words = []
                    for w in words:
                        page_words.append({
                            'left': w['x0'],
                            'top': w['top'],
                            'width': w['width'],
                            'height': w['height'],
                            'text': w['text'].strip()
                        })
                    
                    page_words = sorted(page_words, key=lambda w: (w['top'], w['left']))
                    rows = []
                    current_row = []
                    current_top = -1
                    tolerance = 6
                    
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
                n_boxes = len(data['text'])
                words = []
                for i in range(n_boxes):
                    text = data['text'][i].strip()
                    if text:
                        words.append({
                            'left': data['left'][i],
                            'top': data['top'][i],
                            'width': data['width'][i],
                            'height': data['height'][i],
                            'text': text
                        })
                
                words = sorted(words, key=lambda w: (w['top'], w['left']))
                rows = []
                current_row = []
                current_top = -1
                tolerance = 12
                
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
    benefit_rows = []
    provider_rows = []
    
    all_structured_rows = extract_structured_rows_from_pdf_bytes(file_bytes)
    if not all_structured_rows:
        return pd.DataFrame()

    current_section = "monthly"
    current_policy_year = ""
    current_inception = ""
    current_class_tier = "CLASS VIP"
    
    for row in all_structured_rows:
        row_tokens = [w['text'] for w in row]
        row_text = " ".join(row_tokens)
        line_lower = row_text.lower()
        
        if "confidential" in line_lower:
            continue
            
        if "policy year" in line_lower or "last policy year" in line_lower or "سنة الوثيقة" in line_lower:
            current_policy_year = row_text.strip()
            continue
            
        if "inception" in line_lower:
            date_m = re.search(r'\b(0?[1-9]|[12][0-9]|3[01])[-/](0?[1-9]|1[0-2])[-/](20\d{2})\b', row_text)
            if date_m:
                current_inception = date_m.group(0)
            continue
            
        if "class" in line_lower or "tier" in line_lower or "الفئة" in line_lower:
            clean_class = re.sub(r'(class\s*type|class\s*tier|الفئة[:\s]*)', '', row_text, flags=re.IGNORECASE).strip()
            if not clean_class and ":" in row_text:
                clean_class = row_text.split(":")[-1].strip()
            
            if "class" in row_text.lower() and not clean_class.lower().startswith("class"):
                match_class_full = re.search(r'(class\b.*)', row_text, flags=re.IGNORECASE)
                if match_class_full:
                    clean_class = match_class_full.group(1).strip()
            
            if clean_class and "confidential" not in clean_class.lower():
                clean_class = re.sub(r'\.$', '', clean_class).strip()
                if clean_class:
                    current_class_tier = clean_class
            continue
        elif current_class_tier and not any(k in line_lower for k in ["monthly claim", "breakdown", "top 20", "limit", "coinsurance"]) and len(row_text) > 5 and not re.search(r'\b(20\d{2})\b', row_text):
            if any(w in line_lower for w in ["female", "male", "employee", "without", "divorced", "single"]):
                current_class_tier = current_class_tier + " - " + row_text.strip().rstrip(' .')
                continue
            
        if ("breakdown" in line_lower and "benefit" in line_lower) or any(k in line_lower for k in ["التوزيع حسب المنفعة"]):
            current_section = "benefit"
            continue
        
        if ("top 20" in line_lower) or ("utilised" in line_lower and "provider" in line_lower) or any(k in line_lower for k in ["مزودى الخدمة", "مزوّدي"]):
            current_section = "providers"
            continue

        if any(w in line_lower for w in ["annual claims", "monthly claim", "number of lives at start", "lives at start"]) or re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b', line_lower):
            if not any(k in line_lower for k in ["top 20", "breakdown by benefit", "utilised providers"]):
                current_section = "monthly"

        created_at_ts = pd.Timestamp.now(tz='UTC').isoformat()
        
        # 1. Monthly Claims
        if current_section == "monthly":
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

            date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b|\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\/\-\s]*(20\d{2})\b', line_lower)
            if date_match:
                if date_match.group(1) and date_match.group(2):
                    m_val, y_val = int(date_match.group(1)), int(date_match.group(2))
                    row_date = f"{y_val}-{str(m_val).zfill(2)}"
                elif date_match.group(3) and date_match.group(4):
                    y_val, m_val = int(date_match.group(3)), int(date_match.group(4))
                    row_date = f"{y_val}-{str(m_val).zfill(2)}"
                else:
                    row_date = row_text[:15].strip()
                
                filtered_tokens = []
                for idx, t in enumerate(row_tokens):
                    if idx == 0 and t in ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12']:
                        continue
                    if re.search(r'\b(0?[1-9]|1[0-2])[\/\-]20\d{2}\b|\b20\d{2}[\/\-](0?[1-9]|1[0-2])\b', t):
                        continue
                    filtered_tokens.append(t)
                
                nums = [clean_number(t) for t in filtered_tokens if re.search(r'\d', t)]
                
                if nums:
                    active_lives_val = int(nums[0]) if len(nums) > 0 else 100
                    claims_cnt_val = int(nums[1]) if len(nums) > 1 else 0
                    p_sar = float(nums[2]) if len(nums) > 2 else 0.0
                    p_vat = float(nums[3]) if len(nums) > 3 else 0.0
                    os_cnt = int(nums[4]) if len(nums) > 4 else 0
                    os_sar = float(nums[5]) if len(nums) > 5 else 0.0
                    os_vat = float(nums[6]) if len(nums) > 6 else 0.0

                    monthly_rows.append({
                        "session_id": session_id,
                        "created_at": created_at_ts,
                        "policy_year": current_policy_year or "LAST POLICY YEAR",
                        "inception_date": current_inception or "",
                        "table_header": file_name,
                        "class_tier": current_class_tier,
                        "section_type": "Monthly Claims",
                        "month_code": row_date,
                        "active_lives": active_lives_val,
                        "claims_count": claims_cnt_val,
                        "paid_claims_sar": p_sar,
                        "paid_claims_vat_sar": p_vat,
                        "OS_claims_count": os_cnt,
                        "OS_paid_claims_sar": os_sar,
                        "OS_paid_claims_vat_sar": os_vat,
                        "benefit_name": None,
                        "provider_name": None
                    })
        
        # 2. Breakdown by Benefit
        elif current_section == "benefit":
            all_nums = [(idx, clean_number(t), t) for idx, t in enumerate(row_tokens) if re.search(r'\d', t)]
            
            if len(all_nums) < 3 or "benefit" in line_lower or "confidential" in line_lower or "page" in line_lower:
                continue

            benefit_label = "General Benefit"
            line_full_lower = row_text.lower()
            if "out" in line_full_lower or "out-patient" in line_full_lower:
                benefit_label = "Basic Coverage (Out-Patient)"
            elif "in" in line_full_lower or "in-patient" in line_full_lower:
                benefit_label = "Basic Coverage (In-Patient)"
            elif "dental" in line_full_lower or "dent" in line_full_lower:
                benefit_label = "Dental"
            elif "optical" in line_full_lower or "opt" in line_full_lower:
                benefit_label = "Optical"
            elif "mat" in line_full_lower or "maternity" in line_full_lower:
                benefit_label = "Maternity"
            elif "lab" in line_full_lower:
                benefit_label = "Lab"
            elif "consultation" in line_full_lower or "consulation" in line_full_lower:
                benefit_label = "Consultation"
            elif "pharmacy" in line_full_lower or "med" in line_full_lower:
                benefit_label = "Pharama/Med"
            elif "total" in line_full_lower or "الإجمالي" in line_full_lower:
                benefit_label = "Total"

            real_nums = [item[1] for item in all_nums]
            if len(real_nums) == 5:
                real_nums.insert(0, 0.0)
            elif len(real_nums) >= 6:
                real_nums = real_nums[-6:]

            benefit_rows.append({
                "session_id": session_id,
                "created_at": created_at_ts,
                "policy_year": current_policy_year or "LAST POLICY YEAR",
                "inception_date": current_inception or "",
                "table_header": file_name,
                "class_tier": current_class_tier,
                "section_type": "Breakdown by Benefit",
                "month_code": "Benefit Summary",
                "active_lives": 0,
                "claims_count": int(real_nums[0]) if len(real_nums) > 0 else 0,
                "paid_claims_sar": float(real_nums[1]) if len(real_nums) > 1 else 0.0,
                "paid_claims_vat_sar": float(real_nums[2]) if len(real_nums) > 2 else 0.0,
                "OS_claims_count": int(real_nums[3]) if len(real_nums) > 3 else 0,
                "OS_paid_claims_sar": float(real_nums[4]) if len(real_nums) > 4 else 0.0,
                "OS_paid_claims_vat_sar": float(real_nums[5]) if len(real_nums) > 5 else 0.0,
                "benefit_name": benefit_label,
                "provider_name": None
            })
        
        # 3. Top 20 Providers
        elif current_section == "providers":
            all_nums = [(idx, clean_number(t), t) for idx, t in enumerate(row_tokens) if re.search(r'\d', t)]
            
            if len(all_nums) < 3 or "provider" in line_lower or "confidential" in line_lower or "page" in line_lower:
                continue

            metrics_nums = all_nums[-6:] if len(all_nums) >= 6 else all_nums
            first_metric_idx = metrics_nums[0][0]
            
            prov_tokens = row_tokens[:first_metric_idx]
            prov_name = " ".join(prov_tokens).strip(' .:-')
            if not prov_name or len(prov_name) < 2 or prov_name.isdigit():
                prov_name = row_text[:40].strip()

            real_nums = [item[1] for item in metrics_nums]

            provider_rows.append({
                "session_id": session_id,
                "created_at": created_at_ts,
                "policy_year": current_policy_year or "LAST POLICY YEAR",
                "inception_date": current_inception or "",
                "table_header": file_name,
                "class_tier": current_class_tier,
                "section_type": "Top 20 Providers",
                "month_code": "Provider Summary",
                "active_lives": 0,
                "claims_count": int(real_nums[0]) if len(real_nums) > 0 else 0,
                "paid_claims_sar": float(real_nums[1]) if len(real_nums) > 1 else 0.0,
                "paid_claims_vat_sar": float(real_nums[2]) if len(real_nums) > 2 else 0.0,
                "OS_claims_count": int(real_nums[3]) if len(real_nums) > 3 else 0,
                "OS_paid_claims_sar": float(real_nums[4]) if len(real_nums) > 4 else 0.0,
                "OS_paid_claims_vat_sar": float(real_nums[5]) if len(real_nums) > 5 else 0.0,
                "benefit_name": None,
                "provider_name": prov_name
            })

    all_rows = monthly_rows + benefit_rows + provider_rows
    return pd.DataFrame(all_rows)

st.title("مرصد المطالبات التأمينية الذكي")
st.markdown("رفع تقارير المطالبات ومعالجتها وضخها إلى BigQuery للربط مع Looker Studio.")

uploaded_files = st.file_uploader("رفع تقارير المطالبات المالية للشركة (PDF - متعدد)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if "current_session_id" not in st.session_state:
        st.session_state["current_session_id"] = f"sess_{str(uuid.uuid4())[:8]}"
    
    current_sess = st.session_state["current_session_id"]
    st.info(f"🔑 معرف الجلسة الحالي: **{current_sess}**")
    
    if st.button("معالجة الملفات وضخ البيانات إلى BigQuery", type="primary"):
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
                    for r in records_to_insert:
                        for k, v in r.items():
                            if pd.isna(v):
                                r[k] = None

                    bq_client = get_bq_client(PROJECT_ID)
                    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
                    errors = bq_client.insert_rows_json(table_ref, records_to_insert)
                    
                    if errors == []:
                        st.success(f"تم رفع كافة البيانات بنجاح لـ BigQuery برقم الجلسة: {current_sess}")
                        
                        # توليد رابط Looker Studio بالصيغة المباشرة للبارامتر
                      base_url = "https://datastudio.google.com/reporting/34329d81-4adf-410e-86a9-24713511ec47"
                        params = {"ds14.p_client_session": current_sess}
                        looker_url = f"{base_url}?{urllib.parse.urlencode(params)}"
                        
                        st.markdown("---")
                        st.markdown(f"### 📈 تقرير لوحة المؤشرات جاهز:")
                        st.markdown(f"[اضغط هنا لفتح لوحة البيانات في Looker Studio]({looker_url})", unsafe_allow_html=True)
                    else:
                        st.error(f"خطأ في الحفظ في BigQuery: {errors}")
                except Exception as e:
                    st.error(f"فشل الاتصال بقاعدة البيانات: {str(e)}")
            else:
                st.warning("تعذر استخراج بيانات مطابقة من الملفات المرفوعة.")
