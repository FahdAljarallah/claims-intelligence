import os
import io
import streamlit as st
import pandas as pd
import pdfplumber
import re
import pdf2image
import pytesseract
from PIL import Image

from google.cloud import bigquery
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="مرصد المطالبات التأمينية الذكي", page_icon="📊", layout="wide")

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"
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
    monthly_data = []
    benefit_data = []
    providers_data = []
    
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
        
        for idx, line in enumerate(lines):
            line_lower = line.lower()
            
            # تحديد القسم الحالي بناءً على العناوين الظاهرة في التقرير
            if "monthly claim" in line_lower or "الشهري" in line_lower:
                current_section = "monthly"
                continue
            elif "breakdown by benefit" in line_lower or "التوزيع حسب المنفعة" in line_lower:
                current_section = "benefit"
                continue
            elif "top 20" in line_lower or "utilised providers" in line_lower or "مزودي الخدمة" in line_lower:
                current_section = "providers"
                continue
            
            # استخراج البيانات حسب القسم النشط
            if current_section == "monthly":
                date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b|\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b', line)
                if date_match:
                    if date_match.group(1) and date_match.group(2):
                        row_date = f"{date_match.group(2)}-{date_match.group(1).zfill(2)}"
                    else:
                        row_date = f"{date_match.group(3)}-{date_match.group(4).zfill(2)}"
                    
                    nums = [clean_number(n) for n in re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b', line) if clean_number(n) > 0]
                    claims_val = nums[-1] if nums else 0.0
                    if claims_val > 0:
                        monthly_data.append({
                            "month_code": row_date,
                            "paid_claims": claims_val
                        })
                        
            elif current_section == "benefit":
                nums = [clean_number(n) for n in re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b', line) if clean_number(n) > 0]
                if nums and len(line) > 3:
                    benefit_data.append({
                        "benefit_name": line[:40],
                        "benefit_claims": nums[-1]
                    })
                    
            elif current_section == "providers":
                nums = [clean_number(n) for n in re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b', line) if clean_number(n) > 0]
                if nums and len(line) > 3:
                    providers_data.append({
                        "provider_name": line[:40],
                        "provider_amount": nums[-1]
                    })

    final_rows = []
    
    # 1. Monthly Claims
    if monthly_data:
        seen_months = set()
        for item in monthly_data:
            m = item["month_code"]
            if m not in seen_months:
                seen_months.add(m)
                clm_val = item["paid_claims"]
                final_rows.append({
                    "tenant_id": str(tenant_id),
                    "created_at": pd.Timestamp.now(tz='UTC').isoformat(),
                    "source_file": file_name,
                    "section_type": "Monthly Claims",
                    "item_name": m,
                    "active_lives": int(total_members),
                    "annual_premium_sar": float(current_premium),
                    "paid_claims_sar": float(clm_val),
                    "loss_ratio_pct": float(round((clm_val / (current_premium / 12)) * 100, 2)) if current_premium > 0 else 0.0
                })
                
    # 2. Breakdown by Benefit
    for b in benefit_data:
        final_rows.append({
            "tenant_id": str(tenant_id),
            "created_at": pd.Timestamp.now(tz='UTC').isoformat(),
            "source_file": file_name,
            "section_type": "Breakdown by Benefit",
            "item_name": b["benefit_name"],
            "active_lives": int(total_members),
            "annual_premium_sar": float(current_premium),
            "paid_claims_sar": float(b["benefit_claims"]),
            "loss_ratio_pct": 0.0
        })
        
    # 3. Top 20 Providers
    for p in providers_data:
        final_rows.append({
            "tenant_id": str(tenant_id),
            "created_at": pd.Timestamp.now(tz='UTC').isoformat(),
            "source_file": file_name,
            "section_type": "Top 20 Providers",
            "item_name": p["provider_name"],
            "active_lives": int(total_members),
            "annual_premium_sar": float(current_premium),
            "paid_claims_sar": float(p["provider_amount"]),
            "loss_ratio_pct": 0.0
        })
    
    return pd.DataFrame(final_rows)

# واجهة المستخدم
st.title("مرصد المطالبات التأمينية | التحليل الشامل للأقسام الثلاثة")
st.markdown("منصة تحليل تقارير التأمين — استخراج المطالبات الشهرية، تفاصيل المنافع، وأبرز مقدمي الخدمة بدقة.")

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
    
    if st.button("قراءة وتحليل الملف وتوليد الأقسام الثلاثة", type="primary"):
        with st.spinner(f"جاري معالجة المستند لـ {company_name} وفصل الأقسام التأمينية..."):
            file_bytes = uploaded_file.read()
            df_actual = parse_actual_uploaded_file(file_bytes, uploaded_file.name, tenant_id, total_members, current_premium)
            st.session_state[f"real_dash_{tenant_id}"] = df_actual
            if not df_actual.empty:
                st.success(f"تمت قراءة المستند بنجاح وإنجاز التقسيم بنجاح ({len(df_actual)} سجل إجمالي)!")
            else:
                st.warning("لم يتم العثور على بيانات مطابقة. تأكد من وضوح محتوى ملف الـ PDF.")

    active_key = f"real_dash_{tenant_id}"
    if active_key in st.session_state and not st.session_state[active_key].empty:
        df_res = st.session_state[active_key]
        
        # تقسيم الداتا إلى الأقسام الثلاثة
        df_monthly = df_res[df_res['section_type'] == 'Monthly Claims']
        df_benefit = df_res[df_res['section_type'] == 'Breakdown by Benefit']
        df_providers = df_res[df_res['section_type'] == 'Top 20 Providers']
        
        st.markdown("---")
        st.subheader(f"📊 لوحة القرار التنفيذي للجهة: {company_name}")
        
        # عرض الأقسام عبر تبويبات منفصلة (Tabs)
        tab1, tab2, tab3 = st.tabs([
            "📅 1. المطالبات الشهرية", 
            "🏥 2. التوزيع حسب المنفعة", 
            "🏆 3. أعلى 20 مزود خدمة"
        ])
        
        with tab1:
            st.markdown("### القسم الأول: المطالبات الشهرية (Monthly Claims)")
            total_monthly = df_monthly['paid_claims_sar'].sum() if not df_monthly.empty else 0.0
            st.metric("إجمالي المطالبات الشهرية", f"{total_monthly:,.2f} SAR")
            st.dataframe(df_monthly, use_container_width=True)
            
        with tab2:
            st.markdown("### القسم الثاني: التوزيع حسب المنفعة (Breakdown by Benefit)")
            total_benefit = df_benefit['paid_claims_sar'].sum() if not df_benefit.empty else 0.0
            st.metric("إجمالي مطالبات المنافع", f"{total_benefit:,.2f} SAR")
            st.dataframe(df_benefit, use_container_width=True)
            
        with tab3:
            st.markdown("### القسم الثالث: أبرز مقدمي الخدمة (Top 20 Utilised Providers)")
            total_prov = df_providers['paid_claims_sar'].sum() if not df_providers.empty else 0.0
            st.metric("إجمالي مطالبات المزودين", f"{total_prov:,.2f} SAR")
            st.dataframe(df_providers, use_container_width=True)
        
        st.markdown("---")
        csv_export = df_res.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 تحميل التقرير الكامل للأقسام الثلاثة (CSV)",
            data=csv_export,
            file_name=f"comprehensive_claims_{tenant_id}.csv",
            mime="text/csv",
        )
        
        if st.button("حفظ بيانات الأقسام الثلاثة في مستودع BigQuery المركزي", type="secondary"):
            with st.spinner("جاري الضخ الآمن..."):
                try:
                    bq_client = get_bq_client()
                    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
                    errors = bq_client.insert_rows_json(table_ref, df_res.to_dict(orient="records"))
                    if errors == []:
                        st.success("تم حفظ جميع الأقسام بنجاح في المستودع المركزي!")
                    else:
                        st.error(f"خطأ في الحفظ: {errors}")
                except Exception as e:
                    st.error(f"فشل الاتصال بقاعدة البيانات: {str(e)}")
