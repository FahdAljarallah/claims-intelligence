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
    raw_text = ""
    
    # 1. بما أن الملف عبارة عن نسخة مسحوبة ضوئياً (Scanned Copy)، نبدأ مباشرة بتحويله لصور وتشغيل الـ OCR
    try:
        images = pdf2image.convert_from_bytes(file_bytes)
        for img in images:
            # استخدام اللغتين الإنجليزية والعربية لأن الجداول تحتوي على أرقام وتواريخ إنجليزية ومسميات
            ocr_text = pytesseract.image_to_string(img, lang='eng+ara')
            if ocr_text:
                raw_text += ocr_text + "\n"
    except Exception as e:
        st.error(f"خطأ في تحويل وتشغيل الـ OCR على المستند: {str(e)}")

    # 2. تحليل النص المستخرج عبر الـ OCR للبحث عن صفوف الأشهر والمبالغ
    if raw_text:
        lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
        for idx, line in enumerate(lines):
            # البحث عن صيغ التواريخ الشهرية مثل MM/YYYY أو YYYY/MM (مثال: 11/2025 أو 01/2026)
            date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2})\b|\b(20\d{2})[\/\-](0?[1-9]|1[0-2])\b', line)
            
            if date_match:
                if date_match.group(1) and date_match.group(2):
                    row_date = f"{date_match.group(2)}-{date_match.group(1).zfill(2)}"
                else:
                    row_date = f"{date_match.group(3)}-{date_match.group(4).zfill(2)}"
                
                # تجميع كافة الأرقام الموجودة في السطر والأسطر المجاورة له (بسبب تداخل مسح الـ OCR)
                context_numbers = []
                for scan_idx in range(max(0, idx - 1), min(len(lines), idx + 2)):
                    found_nums = re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b', lines[scan_idx])
                    for num_str in found_nums:
                        val = clean_number(num_str)
                        if val > 0:
                            context_numbers.append(val)
                
                # استخلاص الأعداد والمبالغ بحسب منطق الجدول
                # الأعداد الصغيرة (أقل من 10000) عادة تمثل عدد الموظفين (Lives)
                # الأعداد الكبيرة تمثل المطالبات (Paid Claims)
                lives_val = int(total_members)
                claims_val = 0.0
                
                possible_lives = [n for n in context_numbers if n < 10000 and n != int(row_date.split('-')[0]) and n != int(row_date.split('-')[1])]
                possible_claims = [n for n in context_numbers if n > 1000] # المبالغ التأمينية عادة تتجاوز 1000 ريال
                
                if possible_lives:
                    lives_val = int(possible_lives[0])
                if possible_claims:
                    claims_val = possible_claims[0] # أول مبلغ كبير يمثل المطالبات الصافية
                
                if row_date and claims_val > 0:
                    parsed_months_data.append({
                        "month_code": row_date,
                        "active_lives": lives_val,
                        "paid_claims": claims_val
                    })

    # 3. بناء جدول البيانات النهائي
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
