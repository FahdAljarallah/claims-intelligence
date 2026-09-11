import streamlit as st
import pandas as pd
import pdfplumber
import re
from google.cloud import bigquery
from google.oauth2 import service_account

st.set_page_config(page_title="Universal Claims Ingestion Engine", layout="centered")

st.title("بوابة معالجة وضخ بيانات المطالبات (قبل الضريبة)")
st.markdown("محرك مرن لاستخراج الجداول من مختلف تقارير التأمين وضخها إلى Google BigQuery.")

# 1. إعداد الاتصال بـ BigQuery
PROJECT_ID = st.secrets.get("GCP_PROJECT_ID", "claims-intelligence-507611")
DATASET_ID = "claims_intelligence"

def get_bq_client():
    if "gcp_service_account" in st.secrets:
        creds = service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"])
        return bigquery.Client(credentials=creds, project=PROJECT_ID)
    return bigquery.Client(project=PROJECT_ID)

# 2. تنظيف الأرقام واستبعاد أي تشويش نصي
def parse_number(val):
    if pd.isna(val) or val is None:
        return 0.0
    val_str = str(val).replace(',', '').replace('SAR', '').replace('ر.س', '').strip()
    match = re.search(r'[-+]?\d*\.?\d+', val_str)
    return float(match.group()) if match else 0.0

# 3. محرك الاستخراج المرن (يتعرف على المحتوى بدلالة النصوص وليس موقع الصفحة)
def extract_universal_tables(pdf_file, session_id="SESS_DEFAULT", policy_year="2024/2025"):
    monthly_rows = []
    benefit_rows = []
    provider_rows = []

    benefit_keywords = [
        'outpatient', 'inpatient', 'in patient', 'dental', 'optical', 
        'maternity', 'pharmacy', 'lab', 'consultation', 'عيادات', 'تنويم', 'أسنان', 'بصريات'
    ]
    
    provider_keywords = [
        'hospital', 'center', 'clinic', 'pharmac', 'optics', 'dr.', 
        'مستشفى', 'مركز', 'مجمع', 'صيدلية', 'نظارات', 'د.'
    ]

    with pdfplumber.open(pdf_file) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or len(row) < 3:
                        continue
                    
                    # تنظيف الخلايا
                    row_clean = [str(c).strip().replace('\n', ' ') if c is not None else '' for c in row]
                    first_cell = row_clean[0].lower()
                    second_cell = row_clean[1].lower() if len(row_clean) > 1 else ''

                    # 1. رصد الأداء الشهري (تاريخ بصيغة شهر/سنة مثل 12/2024 أو 2024-12)
                    date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2}|\d{2})\b', row_clean[0]) or \
                                 re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2}|\d{2})\b', second_cell)
                    if date_match and len(row_clean) >= 4:
                        monthly_rows.append({
                            'session_id': session_id,
                            'policy_year': policy_year,
                            'month_code': date_match.group(),
                            'active_lives': int(parse_number(row_clean[1])) if date_match.group() == row_clean[0] else int(parse_number(row_clean[2])),
                            'claims_count': int(parse_number(row_clean[2])) if date_match.group() == row_clean[0] else int(parse_number(row_clean[3])),
                            # سحب القيمة قبل الضريبة (Before VAT)
                            'paid_claims_sar': parse_number(row_clean[3]) if date_match.group() == row_clean[0] else parse_number(row_clean[4]),
                            'paid_claims_vat_sar': parse_number(row_clean[4]) if date_match.group() == row_clean[0] and len(row_clean) > 4 else parse_number(row_clean[5]) if len(row_clean) > 5 else 0.0,
                            'created_at': pd.Timestamp.now()
                        })
                        continue

                    # 2. رصد جدول تفصيل المنافع
                    if any(kw in first_cell for kw in benefit_keywords):
                        benefit_name = row_clean[0]
                        claims_cnt = int(parse_number(row_clean[1])) if len(row_clean) > 1 and row_clean[1].replace(',', '').isdigit() else 0
                        # القيمة قبل الضريبة تسبق دائماً قيمة ما بعد الضريبة
                        amt_before_vat = parse_number(row_clean[2]) if len(row_clean) > 2 else parse_number(row_clean[1])
                        amt_after_vat = parse_number(row_clean[3]) if len(row_clean) > 3 else amt_before_vat
                        
                        benefit_rows.append({
                            'session_id': session_id,
                            'policy_year': policy_year,
                            'benefit_name': benefit_name,
                            'claims_count': claims_cnt,
                            'paid_claims_sar': amt_before_vat,
                            'paid_claims_vat_sar': amt_after_vat,
                            'avg_cost_per_benefit_claim': (amt_before_vat / claims_cnt) if claims_cnt > 0 else 0.0,
                            'created_at': pd.Timestamp.now()
                        })
                        continue

                    # 3. رصد جدول كبار مقدمي الخدمة
                    if any(kw in first_cell for kw in provider_keywords):
                        prov_claims = int(parse_number(row_clean[1])) if len(row_clean) > 1 else 0
                        prov_before_vat = parse_number(row_clean[2]) if len(row_clean) > 2 else 0.0
                        prov_after_vat = parse_number(row_clean[3]) if len(row_clean) > 3 else prov_before_vat
                        
                        provider_rows.append({
                            'session_id': session_id,
                            'policy_year': policy_year,
                            'provider_name': row_clean[0],
                            'claims_count': prov_claims,
                            'paid_claims_sar': prov_before_vat,
                            'paid_claims_vat_sar': prov_after_vat,
                            'created_at': pd.Timestamp.now()
                        })

    df_m = pd.DataFrame(monthly_rows)
    df_b = pd.DataFrame(benefit_rows)
    df_p = pd.DataFrame(provider_rows)

    # حساب ترتيب وحصة مقدم الخدمة قبل الضريبة إذا توفرت بيانات
    if not df_p.empty and 'paid_claims_sar' in df_p.columns:
        total_p_spend = df_p['paid_claims_sar'].sum()
        df_p['rank'] = df_p['paid_claims_sar'].rank(ascending=False, method='min').astype(int)
        df_p['provider_tier_share'] = (df_p['paid_claims_sar'] / total_p_spend) if total_p_spend > 0 else 0.0

    return df_m, df_b, df_p

# 4. دالة الرفع إلى BigQuery
def append_to_bq(client, df, table_name):
    if df.empty:
        return 0
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{table_name}"
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND
    )
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()
    return len(df)

# 5. واجهة الاستخدام والاختبار
col_s1, col_s2 = st.columns(2)
with col_s1:
    session_input = st.text_input("معرّف الجلسة (session_id)", value="RUN_2026_01")
with col_s2:
    year_input = st.text_input("سنة الوثيقة (policy_year)", value="2024/2025")

uploaded_file = st.file_uploader("ارفع أي تقرير تجربة مطالبات (PDF)", type=["pdf"])

if uploaded_file:
    df_m, df_b, df_p = extract_universal_tables(uploaded_file, session_input, year_input)
    
    st.subheader("نتائج فحص واستخراج البيانات (قبل الضريبة - Before VAT)")
    
    tab1, tab2, tab3 = st.tabs(["الأداء الشهري", "تفصيل المنافع", "كبار مقدمي الخدمة"])
    
    with tab1:
        st.write(f"عدد السجلات المكتشفة: {len(df_m)}")
        st.dataframe(df_m)
        
    with tab2:
        st.write(f"عدد المنافع المكتشفة: {len(df_b)}")
        st.dataframe(df_b)
        
    with tab3:
        st.write(f"عدد مقدمي الخدمة المكتشفين: {len(df_p)}")
        st.dataframe(df_p)

    if st.button("تأكيد وضخ البيانات إلى BigQuery"):
        with st.spinner("جاري الضخ إلى Google BigQuery..."):
            try:
                bq_client = get_bq_client()
                n_m = append_to_bq(bq_client, df_m, "monthly_performance")
                n_b = append_to_bq(bq_client, df_b, "benefits_breakdown")
                n_p = append_to_bq(bq_client, df_p, "top_providers")
                st.success(f"تم الإرسال بنجاح! ({n_m} شهري، {n_b} منافع، {n_p} مقدمي خدمة) - المبالغ المعتمدة خالية من الضريبة.")
            except Exception as e:
                st.error(f"فشل الاتصال بـ BigQuery: {str(e)}")
