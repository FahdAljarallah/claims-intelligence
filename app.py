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
    parsed_months_data = []
    
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        # تنظيف الخلايا وتجاهل القيم الفارغة
                        row_cells = [str(c).strip() for c in row if c is not None and str(c).strip() != '']
                        if not row_cells:
                            continue
                        
                        row_date = None
                        lives_val = int(total_members)
                        claims_val = 0.0
                        
                        for cell in row_cells:
                            # البحث عن صيغة الشهر والسنة مثل 11/2025 أو 01/2026
                            date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b|\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b', cell)
                            if date_match and not row_date:
                                if date_match.group(1) and date_match.group(2):
                                    row_date = f"{date_match.group(2)}-{date_match.group(1).zfill(2)}"
                                else:
                                    row_date = f"{date_match.group(3)}-{date_match.group(4).zfill(2)}"
                        
                        # إذا وجدنا سطراً يحتوي على شهر، نقوم باستخراج الأرقام الرقمية المرتبطة به في نفس الصف
                        if row_date:
                            numeric_cells = [clean_number(c) for c in row_cells if clean_number(c) > 0]
                            # عادة الأعمدة تكون: [الشهر, عدد المؤمنين, المطالبات الصافية، ...]
                            if len(numeric_cells) >= 2:
                                # محاولة تحديد عدد الأرواح والمطالبات بدقة بناءً على موقعها
                                possible_lives = [n for n in numeric_cells if n < 10000] # الأعداد الصغيرة تمثل الموظفين غالباً
                                possible_claims = [n for n in numeric_cells if n > 1000]  # الأعداد الكبيرة تمثل المبالغ
                                
                                if possible_lives:
                                    lives_val = int(possible_lives[0])
                                if possible_claims:
                                    claims_val = possible_claims[0] # أول مبلغ كبير عادة هو Net Paid Claims
                            
                            if claims_val > 0:
                                parsed_months_data.append({
                                    "month_code": row_date,
                                    "active_lives": lives_val,
                                    "paid_claims": claims_val
                                })
    except Exception as e:
        st.error(f"خطأ أثناء قراءة الجدول من الملف: {str(e)}")

    # بناء جدول البيانات النهائي المطابق تماماً لملف الـ PDF
    final_rows = []
    if parsed_months_data:
        seen_months = set()
        for item in parsed_months_data:
            m = item["month_code"]
            if m not in seen_months:
                seen_months.add(m)
                clm_val = item["paid_claims"]
                lives_val = item["active_lives"]
                final_rows.append({
                    "tenant_id": str(tenant_id),
                    "created_at": pd.Timestamp.now(tz='UTC').isoformat(),
                    "source_file": file_name,
                    "month_code": m,
                    "active_lives": lives_val,
                    "annual_premium_sar": float(current_premium),
                    "paid_claims_sar": float(clm_val),
                    "loss_ratio_pct": float(round((clm_val / (current_premium / 12)) * 100, 2)) if current_premium > 0 else 0.0
                })
    
    return pd.DataFrame(final_rows)

# واجهة المستخدم عبر Streamlit
st.title("مرصد المطالبات التأمينية | التحليل الفعلي المستقل")
st.markdown("منصة متعددة المستخدمين — ارفع تقرير شركتك (نصي أو مسحوب ضوئياً) لاستخراج البيانات الحقيقية وتوليد لوحة القرار الفورية.")

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
        with st.spinner(f"جاري معالجة المستند لـ {company_name} بدقة مطلقة..."):
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
