import os
import io
import streamlit as st
import pandas as pd
import re
import pdf2image
import pytesseract
from PIL import Image

from google.cloud import bigquery
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="مرصد المطالبات التأمينية الذكي", page_icon="📊", layout="wide")

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"
# يمكنك إنشاء 3 جداول في BigQuery أو جدول موحد بحسب رغبتك
TABLE_ID = "monthly_performance"

@st.cache_resource
def get_bq_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_dict:
        pk = creds_dict["private_key"].replace("\\n", "\n")
        creds_dict["private_key"] = pk
    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)

def clean_number(val):
    try:
        clean_str = str(val).replace('SAR', '').replace('ر.س', '').replace(',', '').strip()
        match = re.search(r'[-+]?\d*\.?\d+', clean_str)
        return float(match.group()) if match else 0.0
    except Exception:
        return 0.0

def parse_actual_uploaded_file(file_bytes, file_name, tenant_id, total_members, current_premium):
    monthly_rows = []
    benefit_rows = []
    provider_rows = []
    
    raw_text = ""
    try:
        images = pdf2image.convert_from_bytes(file_bytes)
        for img in images:
            ocr_text = pytesseract.image_to_string(img, lang='eng+ara')
            if ocr_text:
                raw_text += ocr_text + "\n"
    except Exception as e:
        st.error(f"خطأ في تشغيل الـ OCR: {str(e)}")

    if raw_text:
        lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
        current_section = "unknown"
        current_policy_year = "2025-2026"
        current_class_tier = "Standard / VIP"
        
        for idx, line in enumerate(lines):
            line_lower = line.lower()
            
            # التقاط سنة الوثيقة أو الفئة إذا ظهرت في السطور
            if "policy year" in line_lower or "سنة الوثيقة" in line_lower:
                current_policy_year = line[:30]
                continue
            if "class" in line_lower or "tier" in line_lower or "vip" in line_lower:
                current_class_tier = line[:30]
                continue
                
            # تحديد الأقسام الرئيسية
            if "monthly claim" in line_lower or "المطالبات الشهرية" in line_lower:
                current_section = "monthly"
                continue
            elif "breakdown by benefit" in line_lower or "التوزيع حسب المنفعة" in line_lower:
                current_section = "benefit"
                continue
            elif "top 20" in line_lower or "utilised providers" in line_lower or "مزوّدي الخدمة" in line_lower:
                current_section = "providers"
                continue
            
            created_at_ts = pd.Timestamp.now(tz='UTC').isoformat()
            
            # 1. القسم الأول: Monthly Claims
            if current_section == "monthly":
                date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b|\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b', line)
                if date_match:
                    if date_match.group(1) and date_match.group(2):
                        row_date = f"{date_match.group(2)}-{date_match.group(1).zfill(2)}"
                    else:
                        row_date = f"{date_match.group(3)}-{date_match.group(4).zfill(2)}"
                    
                    nums = [clean_number(n) for n in re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b', line)]
                    if nums:
                        monthly_rows.append({
                            "created_at": created_at_ts,
                            "policy_year": current_policy_year,
                            "table_header": file_name,
                            "month_code": row_date,
                            "class_tier": current_class_tier,
                            "active_lives": int(total_members),
                            "claims_count": int(nums[0]) if len(nums) > 4 else 0,
                            "paid_claims_sar": float(nums[1]) if len(nums) > 4 else float(nums[-1]),
                            "paid_claims_vat_sar": float(nums[2]) if len(nums) > 4 else 0.0,
                            "OS_claims_count": int(nums[3]) if len(nums) > 4 else 0,
                            "OS paid_claims_sar": float(nums[4]) if len(nums) > 5 else 0.0,
                            "OS paid_claims_vat_sar": float(nums[5]) if len(nums) > 6 else 0.0,
                            "section_type": "Monthly Claims"
                        })
            
            # 2. القسم الثاني: Breakdown by Benefit
            elif current_section == "benefit":
                nums = [clean_number(n) for n in re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b', line)]
                if nums and len(line) > 4:
                    benefit_rows.append({
                        "created_at": created_at_ts,
                        "policy_year": current_policy_year,
                        "table_header": file_name,
                        "class_tier": current_class_tier,
                        "benefit_name": line[:40],
                        "claims_count": int(nums[0]) if len(nums) > 2 else 0,
                        "paid_claims_sar": float(nums[1]) if len(nums) > 2 else float(nums[-1]),
                        "paid_claims_vat_sar": float(nums[2]) if len(nums) > 2 else 0.0,
                        "section_type": "Breakdown by Benefit"
                    })
            
            # 3. القسم الثالث: Top 20 utilised providers
            elif current_section == "providers":
                nums = [clean_number(n) for n in re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b', line)]
                if nums and len(line) > 4:
                    provider_rows.append({
                        "created_at": created_at_ts,
                        "policy_year": current_policy_year,
                        "table_header": file_name,
                        "class_tier": current_class_tier,
                        "provider_name": line[:40],
                        "claims_count": int(nums[0]) if len(nums) > 2 else 0,
                        "paid_claims_sar": float(nums[1]) if len(nums) > 2 else float(nums[-1]),
                        "paid_claims_vat_sar": float(nums[2]) if len(nums) > 2 else 0.0,
                        "section_type": "Top 20 Providers"
                    })

    # دمج كل الصفوف في هيكل موحد للـ Session أو إرجاعها
    all_rows = monthly_rows + benefit_rows + provider_rows
    return pd.DataFrame(all_rows)

# واجهة Streamlit
st.title("مرصد المطالبات التأمينية الذكي | الأقسام المتقدمة")
st.markdown("استخراج تفصيلي دقيق للأقسام الثلاثة (Monthly Claims, Breakdown by Benefit, Top 20 Providers).")

col_1, col_2 = st.columns(2)
with col_1:
    company_name = st.text_input("اسم الجهة أو الشركة المستفيدة", value="مؤسسة الأعمال المتقدمة")
with col_2:
    total_members = st.number_input("إجمالي عدد الموظفين المؤمن عليهم (Lives)", min_value=1, max_value=1000000, value=100, step=1)

col_3, _ = st.columns(2)
with col_3:
    current_premium = st.number_input("إجمالي قسط الوثيقة السنوي الحالي (SAR)", min_value=1000.0, max_value=500000000.0, value=800000.0, step=50000.0, format="%.2f")

uploaded_file = st.file_uploader("رفع تقرير المطالبات المالي للشركة (PDF)", type=["pdf"])

if uploaded_file:
    tenant_id = f"tenant_{abs(hash(company_name))}"
    
    if st.button("بدء المعالجة واستخراج الأقسام الثلاثة بدقة", type="primary"):
        with st.spinner(f"جاري قراءة وتحليل المستند لـ {company_name}..."):
            file_bytes = uploaded_file.read()
            df_actual = parse_actual_uploaded_file(file_bytes, uploaded_file.name, tenant_id, total_members, current_premium)
            st.session_state[f"real_dash_{tenant_id}"] = df_actual
            if not df_actual.empty:
                st.success(f"تم بنجاح استخراج البيانات وتوزيعها على الأقسام ({len(df_actual)} سجل إجمالي)!")
            else:
                st.warning("لم يتم العثور على بيانات مطابقة. تأكد من تطابق محتوى الملف.")

    active_key = f"real_dash_{tenant_id}"
    if active_key in st.session_state and not st.session_state[active_key].empty:
        df_res = st.session_state[active_key]
        
        df_monthly = df_res[df_res['section_type'] == 'Monthly Claims']
        df_benefit = df_res[df_res['section_type'] == 'Breakdown by Benefit']
        df_providers = df_res[df_res['section_type'] == 'Top 20 Providers']
        
        st.markdown("---")
        st.subheader(f"📊 لوحة البيانات التشغيلية لـ: {company_name}")
        
        tab1, tab2, tab3 = st.tabs([
            "📅 1. Monthly Claims", 
            "🏥 2. Breakdown by Benefit", 
            "🏆 3. Top 20 Providers"
        ])
        
        with tab1:
            st.markdown("### القسم الأول: المطالبات الشهرية")
            st.dataframe(df_monthly, use_container_width=True)
            
        with tab2:
            st.markdown("### القسم الثاني: التوزيع حسب المنفعة")
            st.dataframe(df_benefit, use_container_width=True)
            
        with tab3:
            st.markdown("### القسم الثالث: أبرز 20 مزود خدمة")
            st.dataframe(df_providers, use_container_width=True)
        
        st.markdown("---")
        csv_export = df_res.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 تحميل التقرير الكامل للأقسام الثلاثة (CSV)",
            data=csv_export,
            file_name=f"structured_claims_{tenant_id}.csv",
            mime="text/csv",
        )
