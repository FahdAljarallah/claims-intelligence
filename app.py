import os
import io
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

def parse_single_file(file_bytes, file_name, total_members):
    monthly_rows = []
    benefit_rows = []
    provider_rows = []
    
    all_structured_rows = extract_structured_rows_from_pdf_bytes(file_bytes)
    if not all_structured_rows:
        return pd.DataFrame()

    current_section = "monthly"
    current_policy_year = "LAST POLICY YEAR"
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
            
        if "class" in line_lower or "الفئة" in line_lower:
            clean_class = re.sub(r'(class\s*type|class\s*tier|الفئة[:\s]*)', '', row_text, flags=re.IGNORECASE).strip()
            if not clean_class and ":" in row_text:
                clean_class = row_text.split(":")[-1].strip()
            if clean_class and "confidential" not in clean_class.lower():
                clean_class = clean_class.rstrip(' .')
                current_class_tier = clean_class
            continue
        elif current_class_tier and not any(k in line_lower for k in ["monthly claim", "breakdown", "top 20", "limit", "coinsurance"]) and len(row_text) > 5 and not re.search(r'\b(20\d{2})\b', row_text):
            if any(w in line_lower for w in ["female", "male", "employee", "without", "divorced", "single"]):
                current_class_tier = current_class_tier + " - " + row_text.strip().rstrip(' .')
                continue
            
        if ("breakdown" in line_lower and "benefit" in line_lower) or any(k in line_lower for k in ["التوزيع حسب المنفعة", "basic coverage", "dental", "optical"]) and current_section != "providers":
            if not any(k in line_lower for k in ["top 20", "utilised"]):
                current_section = "benefit"
                continue
        
        if ("top 20" in line_lower) or ("utilised" in line_lower and "provider" in line_lower) or any(k in line_lower for k in ["مزودى الخدمة", "مزوّدي"]):
            current_section = "providers"
            continue

        if any(w in line_lower for w in ["annual claims", "monthly claim", "number of lives at start", "lives at start"]) or re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b', line_lower):
            if not any(k in line_lower for k in ["top 20", "breakdown by benefit", "utilised providers"]):
                current_section = "monthly"

        created_at_ts = pd.Timestamp.now(tz='UTC').isoformat()
        
        # 1. القسم الأول: Monthly Claims
        if current_section == "monthly":
            if "number of lives at start" in line_lower or "lives at start" in line_lower:
                nums_lives = [clean_number(t) for t in row_tokens if re.search(r'\d', t)]
                lives_val = int(nums_lives[0]) if nums_lives else int(total_members)
                monthly_rows.append({
                    "created_at": created_at_ts,
                    "policy_year": current_policy_year,
                    "table_header": file_name,
                    "month_code": "Number of lives at start",
                    "class_tier": current_class_tier,
                    "active_lives": lives_val,
                    "claims_count": 0,
                    "paid_claims_sar": 0.0,
                    "paid_claims_vat_sar": 0.0,
                    "OS_claims_count": 0,
                    "OS paid_claims_sar": 0.0,
                    "OS paid_claims_vat_sar": 0.0,
                    "section_type": "Monthly Claims"
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
                date_excluded = False
                for t in row_tokens:
                    if not date_excluded and re.search(r'\b(0?[1-9]|1[0-2])[\/\-]20\d{2}\b|\b20\d{2}[\/\-](0?[1-9]|1[0-2])\b', t):
                        date_excluded = True
                        continue
                    filtered_tokens.append(t)
                
                nums = [clean_number(t) for t in filtered_tokens if re.search(r'\d', t)]
                
                if nums:
                    active_lives_val = int(nums[0]) if len(nums) > 0 else int(total_members)
                    claims_cnt_val = int(nums[1]) if len(nums) > 1 else 0
                    p_sar = float(nums[2]) if len(nums) > 2 else 0.0
                    p_vat = float(nums[3]) if len(nums) > 3 else 0.0
                    os_cnt = int(nums[4]) if len(nums) > 4 else 0
                    os_sar = float(nums[5]) if len(nums) > 5 else 0.0
                    os_vat = float(nums[6]) if len(nums) > 6 else 0.0

                    monthly_rows.append({
                        "created_at": created_at_ts,
                        "policy_year": current_policy_year,
                        "table_header": file_name,
                        "month_code": row_date,
                        "class_tier": current_class_tier,
                        "active_lives": active_lives_val,
                        "claims_count": claims_cnt_val,
                        "paid_claims_sar": p_sar,
                        "paid_claims_vat_sar": p_vat,
                        "OS_claims_count": os_cnt,
                        "OS paid_claims_sar": os_sar,
                        "OS paid_claims_vat_sar": os_vat,
                        "section_type": "Monthly Claims"
                    })
        
        # 2. القسم الثاني: Breakdown by Benefit (شامل لجميع المنافع دون استثناء)
        elif current_section == "benefit":
            benefit_label = "General Benefit"
            line_full_lower = row_text.lower()
            
            if "out" in line_full_lower or "out-patient" in line_full_lower or "basic coverage (out" in line_full_lower:
                benefit_label = "Basic Coverage (Out-Patient)"
            elif "in" in line_full_lower or "in-patient" in line_full_lower or "basic coverage (in" in line_full_lower:
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

            nums = [clean_number(t) for t in row_tokens if re.search(r'\d', t)]
            
            if nums and len(nums) >= 4:
                benefit_rows.append({
                    "created_at": created_at_ts,
                    "policy_year": current_policy_year,
                    "table_header": file_name,
                    "month_code": "Benefit Summary",
                    "class_tier": current_class_tier,
                    "active_lives": 0,
                    "claims_count": int(nums[0]),
                    "paid_claims_sar": float(nums[1]),
                    "paid_claims_vat_sar": float(nums[2]),
                    "OS_claims_count": int(nums[3]),
                    "OS paid_claims_sar": float(nums[4]) if len(nums) > 4 else 0.0,
                    "OS paid_claims_vat_sar": float(nums[5]) if len(nums) > 5 else 0.0,
                    "section_type": "Breakdown by Benefit",
                    "benefit_name": benefit_label
                })
        
        # 3. القسم الثالث: Top 20 Providers
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
                "created_at": created_at_ts,
                "policy_year": current_policy_year,
                "table_header": file_name,
                "class_tier": current_class_tier,
                "claims_count": int(real_nums[0]) if len(real_nums) > 0 else 0,
                "paid_claims_sar": float(real_nums[1]) if len(real_nums) > 1 else 0.0,
                "paid_claims_vat_sar": float(real_nums[2]) if len(real_nums) > 2 else 0.0,
                "OS_claims_count": int(real_nums[3]) if len(real_nums) > 3 else 0,
                "OS paid_claims_sar": float(real_nums[4]) if len(real_nums) > 4 else 0.0,
                "OS paid_claims_vat_sar": float(real_nums[5]) if len(real_nums) > 5 else 0.0,
                "section_type": "Top 20 Providers",
                "provider_name": prov_name
            })

    all_rows = monthly_rows + benefit_rows + provider_rows
    return pd.DataFrame(all_rows)

# واجهة Streamlit ديناميكية بالكامل لدعم الملفات المتعددة
st.title("مرصد المطالبات التأمينية | المعاينة والربط الذكي")
st.markdown("استخراج الأقسام الثلاثة ديناميكياً ودعم رفع ملفات متعددة (Digital-Native & Scanned PDFs).")

col_1, col_2 = st.columns(2)
with col_1:
    company_name = st.text_input("اسم الجهة أو الشركة المستفيدة", value="مؤسسة الأعمال المتقدمة")
with col_2:
    total_members = st.number_input("إجمالي عدد الموظفين المؤمن عليهم (Lives)", min_value=1, max_value=1000000, value=100, step=1)

col_3, col_4 = st.columns(2)
with col_3:
    project_id_input = st.text_input("Google Cloud Project ID", value="claims-intelligence-507611")
with col_4:
    dataset_id_input = st.text_input("BigQuery Dataset ID", value="claims_intelligence")

table_id_input = st.text_input("BigQuery Table ID", value="monthly_performance")

uploaded_files = st.file_uploader("رفع تقارير المطالبات المالية للشركة (PDF - متعدد)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    tenant_id = f"tenant_{abs(hash(company_name))}"
    
    if st.button("معالجة كافة الملفات المرفوعة واستخراج الجداول", type="primary"):
        with st.spinner("جاري قراءة كافة الأقسام وجلب كافة تفاصيل المنافع..."):
            all_dfs = []
            for uploaded_file in uploaded_files:
                file_bytes = uploaded_file.read()
                df_single = parse_single_file(file_bytes, uploaded_file.name, total_members)
                if not df_single.empty:
                    all_dfs.append(df_single)
            
            if all_dfs:
                df_actual = pd.concat(all_dfs, ignore_index=True)
                st.session_state[f"real_dash_{tenant_id}"] = df_actual
                st.success(f"تمت معالجة كافة الملفات بنجاح! إجمالي السجلات المستخرجة المدمجة: {len(df_actual)}")
            else:
                st.warning("تعذر استخراج بيانات مطابقة من الملفات المرفوعة، تأكد من صحة الـ PDF.")

    active_key = f"real_dash_{tenant_id}"
    if active_key in st.session_state and not st.session_state[active_key].empty:
        df_res = st.session_state[active_key]
        
        df_monthly = df_res[df_res['section_type'] == 'Monthly Claims']
        df_benefit = df_res[df_res['section_type'] == 'Breakdown by Benefit']
        df_providers = df_res[df_res['section_type'] == 'Top 20 Providers']
        
        st.markdown("---")
        st.subheader("📋 معاينة البيانات المستخرجة لجميع الملفات للأقسام الثلاثة")
        
        tab1, tab2, tab3 = st.tabs([
            "📅 1. Monthly Claims", 
            "🏥 2. Breakdown by Benefit", 
            "🏆 3. Top 20 Providers"
        ])
        
        with tab1:
            st.dataframe(df_monthly, use_container_width=True)
        with tab2:
            st.dataframe(df_benefit, use_container_width=True)
        with tab3:
            st.dataframe(df_providers, use_container_width=True)
        
        st.markdown("---")
        col_dl, col_bq = st.columns(2)
        
        with col_dl:
            csv_export = df_res.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 تحميل التقرير المدمج الكامل بصيغة (CSV)",
                data=csv_export,
                file_name=f"claims_export_all_{tenant_id}.csv",
                mime="text/css",
                use_container_width=True
            )
            
        with col_bq:
            if st.button("🚀 ضخ البيانات المدمجة إلى BigQuery مباشرة", type="secondary", use_container_width=True):
                with st.spinner("جاري الإرسال الآمن إلى المستودع المركزي..."):
                    try:
                        bq_client = get_bq_client(project_id_input)
                        table_ref = f"{project_id_input}.{dataset_id_input}.{table_id_input}"
                        errors = bq_client.insert_rows_json(table_ref, df_res.to_dict(orient="records"))
                        if errors == []:
                            st.success("تم رفع كافة البيانات بنجاح إلى جدول BigQuery ومجهزة للربط بـ Looker Studio!")
                        else:
                            st.error(f"خطأ في الحفظ: {errors}")
                    except Exception as e:
                        st.error(f"فشل الاتصال بقاعدة البيانات: {str(e)}")
