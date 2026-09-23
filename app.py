import os
import io
import streamlit as st
import pandas as pd
import re
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

def extract_structured_rows_from_image(img):
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
        
    return rows

def parse_actual_uploaded_file(file_bytes, file_name, total_members):
    monthly_rows = []
    benefit_rows = []
    provider_rows = []
    
    try:
        images = pdf2image.convert_from_bytes(file_bytes)
        all_structured_rows = []
        for img in images:
            page_rows = extract_structured_rows_from_image(img)
            all_structured_rows.extend(page_rows)
    except Exception as e:
        st.error(f"خطأ في تشغيل الـ OCR المكاني: {str(e)}")
        return pd.DataFrame()

    current_section = "unknown"
    current_policy_year = ""
    current_class_tier = ""
    
    for row in all_structured_rows:
        row_tokens = [w['text'] for w in row]
        row_text = " ".join(row_tokens)
        line_lower = row_text.lower()
        
        if "confidential" in line_lower:
            continue
            
        if "policy year" in line_lower or "last policy year" in line_lower or "سنة الوثيقة" in line_lower:
            current_policy_year = row_text.strip()
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
                clean_class = re.sub(r'\.$', '', clean_class)
                current_class_tier = clean_class
            continue
        elif current_class_tier and not any(k in line_lower for k in ["monthly claim", "breakdown", "top 20", "limit", "coinsurance"]) and len(row_text) > 5 and not re.search(r'\b(20\d{2})\b', row_text):
            if any(w in line_lower for w in ["female", "male", "employee", "without", "divorced"]):
                current_class_tier = current_class_tier + " " + row_text.strip()
                continue
            
        if any(k in line_lower for k in ["monthly claim", "number of lives at start", "المطالبات الشهرية"]):
            current_section = "monthly"
        elif any(k in line_lower for k in ["breakdown by benefit", "التوزيع حسب المنفعة"]):
            current_section = "benefit"
            continue
        elif any(k in line_lower for k in ["top 20", "utilised providers", "مزودى الخدمة", "مزوّدي"]):
            current_section = "providers"
            continue
        
        created_at_ts = pd.Timestamp.now(tz='UTC').isoformat()
        
        # 1. القسم الأول: Monthly Claims
        if current_section == "monthly":
            if "number of lives at start" in line_lower:
                nums_lives = [clean_number(t) for t in row_tokens if re.search(r'\d', t)]
                lives_val = int(nums_lives[0]) if nums_lives else int(total_members)
                monthly_rows.append({
                    "created_at": created_at_ts,
                    "policy_year": current_policy_year or "LAST POLICY YEAR",
                    "table_header": file_name,
                    "month_code": "Number of lives at start",
                    "class_tier": current_class_tier or "CLASS GENERAL",
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

            date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b|\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b', row_text)
            if date_match:
                if date_match.group(1) and date_match.group(2):
                    m_val, y_val = int(date_match.group(1)), int(date_match.group(2))
                    row_date = f"{y_val}-{str(m_val).zfill(2)}"
                else:
                    y_val, m_val = int(date_match.group(3)), int(date_match.group(4))
                    row_date = f"{y_val}-{str(m_val).zfill(2)}"
                
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
                        "policy_year": current_policy_year or "LAST POLICY YEAR",
                        "table_header": file_name,
                        "month_code": row_date,
                        "class_tier": current_class_tier or "CLASS GENERAL",
                        "active_lives": active_lives_val,
                        "claims_count": claims_cnt_val,
                        "paid_claims_sar": p_sar,
                        "paid_claims_vat_sar": p_vat,
                        "OS_claims_count": os_cnt,
                        "OS paid_claims_sar": os_sar,
                        "OS paid_claims_vat_sar": os_vat,
                        "section_type": "Monthly Claims"
                    })
        
        # 2. القسم الثاني: Breakdown by Benefit
        elif current_section == "benefit":
            benefit_label = None
            line_full_lower = row_text.lower()
            
            if "mat" in line_full_lower or "maternity" in line_full_lower:
                benefit_label = "Maternity"
            elif "out" in line_full_lower or "out-patient" in line_full_lower:
                benefit_label = "Basic Coverage (Out-Patient)"
            elif "in" in line_full_lower or "in-patient" in line_full_lower:
                benefit_label = "Basic Coverage (In-Patient)"
            elif "dental" in line_full_lower:
                benefit_label = "Dental"
            elif "optical" in line_full_lower:
                benefit_label = "Optical"
            elif "lab" in line_full_lower:
                benefit_label = "Lab"
            elif "consultation" in line_full_lower or "consulation" in line_full_lower:
                benefit_label = "Consultation"
            elif "pharmacy" in line_full_lower or "med" in line_full_lower:
                benefit_label = "Pharama/Med"
            elif "total" in line_full_lower or "الإجمالي" in line_full_lower:
                benefit_label = "Total"

            nums = [clean_number(t) for t in row_tokens if re.search(r'\d', t)]
            
            if nums and len(nums) >= 4 and benefit_label:
                benefit_rows.append({
                    "created_at": created_at_ts,
                    "policy_year": current_policy_year or "LAST POLICY YEAR",
                    "table_header": file_name,
                    "month_code": "Benefit Summary",
                    "class_tier": current_class_tier or "CLASS GENERAL",
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
        
        # 3. القسم الثالث: Top 20 Providers (مع حماية مؤشرات القراءة الآمنة)
        elif current_section == "providers":
            nums = [clean_number(t) for t in row_tokens if re.search(r'\d', t)]
            if nums and len(row_text) > 4 and "provider" not in line_lower:
                non_num_tokens = [t for t in row_tokens if not re.search(r'\d', t) and t.lower() not in ['confidential', 'page']]
                prov_name = " ".join(non_num_tokens).strip(' .:-')
                if not prov_name:
                    prov_name = row_text[:40].strip()

                provider_rows.append({
                    "created_at": created_at_ts,
                    "policy_year": current_policy_year or "LAST POLICY YEAR",
                    "table_header": file_name,
                    "class_tier": current_class_tier or "CLASS GENERAL",
                    "claims_count": int(nums[0]) if len(nums) > 0 else 0,
                    "paid_claims_sar": float(nums[1]) if len(nums) > 1 else 0.0,
                    "paid_claims_vat_sar": float(nums[2]) if len(nums) > 2 else 0.0,
                    "OS_claims_count": int(nums[3]) if len(nums) > 3 else 0,
                    "OS paid_claims_sar": float(nums[4]) if len(nums) > 4 else 0.0,
                    "OS paid_claims_vat_sar": float(nums[5]) if len(nums) > 5 else 0.0,
                    "section_type": "Top 20 Providers",
                    "provider_name": prov_name
                })

    all_rows = monthly_rows + benefit_rows + provider_rows
    return pd.DataFrame(all_rows)

# واجهة Streamlit ديناميكية بالكامل
st.title("مرصد المطالبات التأمينية | المعاينة والربط الذكي")
st.markdown("استخراج الأقسام الثلاثة ديناميكياً والربط بـ BigQuery بدون أي قيم صلبة.")

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

uploaded_file = st.file_uploader("رفع تقرير المطالبات المالي للشركة (PDF)", type=["pdf"])

if uploaded_file:
    tenant_id = f"tenant_{abs(hash(company_name))}"
    
    if st.button("معالجة الملف واستخراج الجداول", type="primary"):
        with st.spinner("جاري قراءة الملف وتطبيق معالجة الفهارس الآمنة..."):
            file_bytes = uploaded_file.read()
            df_actual = parse_actual_uploaded_file(file_bytes, uploaded_file.name, total_members)
            st.session_state[f"real_dash_{tenant_id}"] = df_actual
            if not df_actual.empty:
                st.success(f"تمت المعالجة بنجاح! إجمالي السجلات المستخرجة: {len(df_actual)}")
            else:
                st.warning("تعذر استخراج بيانات مطابقة، تأكد من ملف الـ PDF.")

    active_key = f"real_dash_{tenant_id}"
    if active_key in st.session_state and not st.session_state[active_key].empty:
        df_res = st.session_state[active_key]
        
        df_monthly = df_res[df_res['section_type'] == 'Monthly Claims']
        df_benefit = df_res[df_res['section_type'] == 'Breakdown by Benefit']
        df_providers = df_res[df_res['section_type'] == 'Top 20 Providers']
        
        st.markdown("---")
        st.subheader("📋 معاينة البيانات المستخرجة للأقسام الثلاثة")
        
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
                label="📥 تحميل التقرير الكامل بصيغة (CSV)",
                data=csv_export,
                file_name=f"claims_export_{tenant_id}.csv",
                mime="text/css",
                use_container_width=True
            )
            
        with col_bq:
            if st.button("🚀 ضخ البيانات إلى BigQuery مباشرة", type="secondary", use_container_width=True):
                with st.spinner("جاري الإرسال الآمن إلى المستودع المركزي..."):
                    try:
                        bq_client = get_bq_client(project_id_input)
                        table_ref = f"{project_id_input}.{dataset_id_input}.{table_id_input}"
                        errors = bq_client.insert_rows_json(table_ref, df_res.to_dict(orient="records"))
                        if errors == []:
                            st.success("تم رفع البيانات بنجاح إلى جدول BigQuery ومجهزة للربط بـ Looker Studio!")
                        else:
                            st.error(f"خطأ في الحفظ: {errors}")
                    except Exception as e:
                        st.error(f"فشل الاتصال بقاعدة البيانات: {str(e)}")
