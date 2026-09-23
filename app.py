import os
import io
import streamlit as st
import pandas as pd
import pdfplumber
import re

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

# محرك قراءة الملف الفعلي واستخراج الأرقام ديناميكياً بدقة
def parse_actual_uploaded_file(file_bytes, file_name, tenant_id, total_members, current_premium):
    parsed_months_data = []
    raw_text = ""
    
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                t = page.extract_text()
                if t:
                    raw_text += t + "\n"
                
                # المحاولة الأولى: استخراج الجداول المنظمة
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            row_cells = [str(c).strip() for c in row if c is not None and str(c).strip() != '']
                            if not row_cells:
                                continue
                            
                            row_date = None
                            row_claims = 0.0
                            for cell in row_cells:
                                date_match = re.search(r'\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b|\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b', cell)
                                if date_match and not row_date:
                                    if date_match.group(1) and date_match.group(2):
                                        row_date = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}"
                                    else:
                                        row_date = f"{date_match.group(4)}-{date_match.group(3).zfill(2)}"
                                
                                num_val = clean_number(cell)
                                if num_val > 100:
                                    row_claims = num_val
                            
                            if row_date and row_claims > 0:
                                parsed_months_data.append({
                                    "month_code": row_date,
                                    "extracted_claims": row_claims
                                })
    except Exception as e:
        st.error(f"خطأ في قراءة الملف الفعلي: {str(e)}")

    # المحاولة الثانية (الاحتياطية): إذا لم تصد الجداول شيئاً، نقوم بمسح النص الخام سطر بسطر
    if not parsed_months_data and raw_text:
        lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
        for idx, line in enumerate(lines):
            date_match = re.search(r'\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b|\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b', line)
            if date_match:
                if date_match.group(1) and date_match.group(2):
                    m_code = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}"
                else:
                    m_code = f"{date_match.group(4)}-{date_match.group(3).zfill(2)}"
                
                # البحث عن الأرقام القريبة في نفس السطر أو الأسطر المجاورة
                nums_in_context = []
                for scan_idx in range(idx, min(idx + 3, len(lines))):
                    found_nums = re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b', lines[scan_idx])
                    for num_str in found_nums:
                        val = clean_number(num_str)
                        if val > 100:
                            nums_in_context.append(val)
                
                if nums_in_context:
                    parsed_months_data.append({
                        "month_code": m_code,
                        "extracted_claims": nums_in_context[0]
                    })

    # بناء الجدول النهائي
    final_rows = []
    if parsed_months_data:
        seen_months = set()
        for item in parsed_months_data:
            m = item["month_code"]
            if m not in seen_months:
                seen_months.add(m)
                clm_val = item["extracted_claims"]
                final_rows.append({
                    "tenant_id": str(tenant_id),
                    "created_at": pd.Timestamp.now(tz='UTC').isoformat(),
                    "source_file": file_name,
                    "month_code": m,
                    "active_lives": int(total_members),
                    "annual_premium_sar": float(current_premium),
                    "paid_claims_sar": float(clm_val),
                    "loss_ratio_pct": float(round((clm_val / (current_premium / 12)) * 100, 2)) if current_premium > 0 else 0.0
                })
    
    return pd.DataFrame(final_rows)

st.title("مرصد المطالبات التأمينية | التحليل الفعلي المستقل")
st.markdown("منصة متعددة المستخدمين — ارفع تقرير شركتك لاستخراج البيانات الحقيقية وتوليد لوحة القرار الفورية.")

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
    
    if st.button("قراءة وتحليل الملف الفعلي وتوليد اللوحة", type="primary"):
        with st.spinner(f"جاري معالجة المستند الفعلي لـ {company_name} بدقة مطلقة..."):
            file_bytes = uploaded_file.read()
            df_actual = parse_actual_uploaded_file(file_bytes, uploaded_file.name, tenant_id, total_members, current_premium)
            st.session_state[f"real_dash_{tenant_id}"] = df_actual
            if not df_actual.empty:
                st.success(f"تمت قراءة المستند بنجاح واستخراج {len(df_actual)} سجل شهري!")
            else:
                st.warning("لم يتم العثور على جداول شهرية مطابقة. تأكد من أن ملف الـ PDF يحتوي على جدول ببيانات الأشهر والمطالبات.")

    active_key = f"real_dash_{tenant_id}"
    if active_key in st.session_state and not st.session_state[active_key].empty:
        df_res = st.session_state[active_key]
        
        st.markdown("---")
        st.subheader(f"📊 لوحة القرار التنفيذي الفعلي لـ: {company_name}")
        
        total_paid = df_res['paid_claims_sar'].sum()
        target_savings = total_paid * 0.15
        
        kpi1, kpi2 = st.columns(2)
        kpi1.metric("إجمالي المطالبات المستخرجة فعلياً", f"{total_paid:,.2f} SAR")
        kpi2.metric("الوفورات المستهدفة للتفاوض (15%)", f"{target_savings:,.2f} SAR", "فرصة لخفض الأقساط")
        
        st.markdown("### جدول البيانات المستخرجة من الملف المرفق")
        st.dataframe(df_res, use_container_width=True)
        
        csv_export = df_res.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 تحميل التقرير المستخلص (CSV)",
            data=csv_export,
            file_name=f"actual_claims_{tenant_id}.csv",
            mime="text/csv",
        )
        
        if st.button("حفظ بيانات الجهة في مستودع BigQuery المركزي", type="secondary"):
            with st.spinner("جاري الضخ الآمن..."):
                try:
                    bq_client = get_bq_client()
                    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
                    errors = bq_client.insert_rows_json(table_ref, df_res.to_dict(orient="records"))
                    if errors == []:
                        st.success("تم حفظ البيانات الفعلية للمستخدم بنجاح في المستودع المركزي!")
                    else:
                        st.error(f"خطأ في الحفظ: {errors}")
                except Exception as e:
                    st.error(f"فشل الاتصال بقاعدة البيانات: {str(e)}")
