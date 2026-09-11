import streamlit as st
import pandas as pd
import pdfplumber
import uuid
import re
from google.cloud import bigquery
from google.oauth2 import service_account

st.set_page_config(page_title="Claims Intelligence & Renewal Engine", layout="wide")

# 1. إعدادات BigQuery ولوحة Looker Studio
PROJECT_ID = st.secrets.get("GCP_PROJECT_ID", "claims-intelligence-507611")
DATASET_ID = "claims_intelligence"
LOOKER_STUDIO_URL = "https://lookerstudio.google.com/embed/reporting/YOUR_REPORT_ID/page/YOUR_PAGE_ID"

def get_bq_client():
    if "gcp_service_account" in st.secrets:
        creds = service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"])
        return bigquery.Client(credentials=creds, project=PROJECT_ID)
    return bigquery.Client(project=PROJECT_ID)

def clean_num(val):
    if pd.isna(val) or val is None:
        return 0.0
    val_str = str(val).replace(',', '').replace('SAR', '').replace('ر.س', '').strip()
    match = re.search(r'[-+]?\d*\.?\d+', val_str)
    return float(match.group()) if match else 0.0

# 2. محرك التفكيك والتوزيع التلقائي على الجداول الثلاثة (قبل الضريبة)
def parse_and_distribute(uploaded_file, session_id, policy_year):
    monthly_rows, benefit_rows, provider_rows = [], [], []

    benefit_kws = ['outpatient', 'inpatient', 'in patient', 'dental', 'optical', 'maternity', 'pharmacy', 'lab', 'consultain', 'consultation']
    provider_kws = ['hospital', 'center', 'clinic', 'pharmaci', 'optics', 'dr.', 'dallah', 'alnahdi', 'magrabi']

    file_ext = uploaded_file.name.split('.')[-1].lower()

    if file_ext == "pdf":
        with pdfplumber.open(uploaded_file) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if not row or len(row) < 3:
                            continue
                        row_c = [str(c).strip().replace('\n', ' ') if c is not None else '' for c in row]
                        c0, c1 = row_c[0].lower(), row_c[1].lower() if len(row_c) > 1 else ''

                        # جدول الأداء الشهري
                        date_match = re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2}|\d{2})\b', row_c[0]) or \
                                     re.search(r'\b(0?[1-9]|1[0-2])[\/\-](20\d{2}|\d{2})\b', c1)
                        if date_match and len(row_c) >= 4:
                            is_first = (date_match.group() == row_c[0])
                            monthly_rows.append({
                                'session_id': session_id,
                                'policy_year': policy_year,
                                'month_code': date_match.group(),
                                'active_lives': int(clean_num(row_c[1])) if is_first else int(clean_num(row_c[2])),
                                'claims_count': int(clean_num(row_c[2])) if is_first else int(clean_num(row_c[3])),
                                'paid_claims_sar': clean_num(row_c[3]) if is_first else clean_num(row_c[4]),
                                'paid_claims_vat_sar': clean_num(row_c[4]) if is_first and len(row_c) > 4 else clean_num(row_c[5]) if len(row_c) > 5 else 0.0,
                                'created_at': pd.Timestamp.now()
                            })
                            continue

                        # جدول تفصيل المنافع
                        if any(k in c0 for k in benefit_kws):
                            cnt = int(clean_num(row_c[1])) if len(row_c) > 1 and row_c[1].replace(',', '').isdigit() else 0
                            amt = clean_num(row_c[2]) if len(row_c) > 2 else clean_num(row_c[1])
                            amt_vat = clean_num(row_c[3]) if len(row_c) > 3 else amt
                            benefit_rows.append({
                                'session_id': session_id,
                                'policy_year': policy_year,
                                'benefit_name': row_c[0],
                                'claims_count': cnt,
                                'paid_claims_sar': amt,
                                'paid_claims_vat_sar': amt_vat,
                                'avg_cost_per_benefit_claim': (amt / cnt) if cnt > 0 else 0.0,
                                'created_at': pd.Timestamp.now()
                            })
                            continue

                        # جدول مقدمي الخدمة
                        if any(k in c0 for k in provider_kws):
                            p_cnt = int(clean_num(row_c[1])) if len(row_c) > 1 else 0
                            p_amt = clean_num(row_c[2]) if len(row_c) > 2 else 0.0
                            p_vat = clean_num(row_c[3]) if len(row_c) > 3 else p_amt
                            provider_rows.append({
                                'session_id': session_id,
                                'policy_year': policy_year,
                                'provider_name': row_c[0],
                                'claims_count': p_cnt,
                                'paid_claims_sar': p_amt,
                                'paid_claims_vat_sar': p_vat,
                                'created_at': pd.Timestamp.now()
                            })
    else:
        # قراءة الإكسل وتوزيعه
        xls = pd.ExcelFile(uploaded_file)
        for s in xls.sheet_names:
            df_temp = pd.read_excel(xls, sheet_name=s)
            s_low = s.lower()
            if any(k in s_low for k in ['month', 'شهري']):
                df_temp['session_id'], df_temp['policy_year'] = session_id, policy_year
                monthly_rows.extend(df_temp.to_dict(orient='records'))
            elif any(k in s_low for k in ['benefit', 'منافع']):
                df_temp['session_id'], df_temp['policy_year'] = session_id, policy_year
                benefit_rows.extend(df_temp.to_dict(orient='records'))
            elif any(k in s_low for k in ['provider', 'مقدم']):
                df_temp['session_id'], df_temp['policy_year'] = session_id, policy_year
                provider_rows.extend(df_temp.to_dict(orient='records'))

    df_m = pd.DataFrame(monthly_rows)
    df_b = pd.DataFrame(benefit_rows)
    df_p = pd.DataFrame(provider_rows)

    if not df_p.empty and 'paid_claims_sar' in df_p.columns:
        tot = df_p['paid_claims_sar'].sum()
        df_p['rank'] = df_p['paid_claims_sar'].rank(ascending=False, method='min').astype(int)
        df_p['provider_tier_share'] = (df_p['paid_claims_sar'] / tot) if tot > 0 else 0.0

    return df_m, df_b, df_p

def push_to_bq(client, df, table_name):
    if df.empty:
        return 0
    ref = f"{PROJECT_ID}.{DATASET_ID}.{table_name}"
    job_cfg = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND)
    job = client.load_table_from_dataframe(df, ref, job_config=job_cfg)
    job.result()
    return len(df)

# 3. واجهة المستخدم التشغيلية
st.title("بوابة ذكاء المطالبات والتجديد التأميني")
st.markdown("معالجة آلية لتقارير المطالبات، ضخ مباشر في مستودع البيانات، وتوليد فوري للوحة القرار.")

# توليد معرّف جلسة فريد لكل عملية رفع
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = f"SESS_{uuid.uuid4().hex[:8].upper()}"

c1, c2 = st.columns([1, 1])
with c1:
    session_id = st.text_input("معرّف الجلسة (Session ID)", value=st.session_state.current_session_id)
with c2:
    policy_year = st.text_input("سنة الوثيقة (Policy Year)", value="2024/2025")

up_file = st.file_uploader("ارفع تقرير المطالبات (PDF أو Excel)", type=["pdf", "xlsx", "xls"])

if up_file:
    df_m, df_b, df_p = parse_and_distribute(up_file, session_id, policy_year)
    
    st.write(f"المؤشرات المستخرجة قبل الضريبة: الأداء الشهري ({len(df_m)} سجل) | المنافع ({len(df_b)} سجل) | كبار مقدمي الخدمة ({len(df_p)} سجل)")

    if st.button("ضخ البيانات وفتح لوحة القيادة المفلترة", type="primary"):
        with st.spinner("جاري التحديث والتوجيه للوحة القرار..."):
            try:
                bq = get_bq_client()
                push_to_bq(bq, df_m, "monthly_performance")
                push_to_bq(bq, df_b, "benefits_breakdown")
                push_to_bq(bq, df_p, "top_providers")
                
                st.success("تم تحديث مستودع البيانات بنجاح!")
                
                # إنشاء رابط Looker Studio ممرر إليه session_id كفلتر تلقائي
                filtered_url = f"{LOOKER_STUDIO_URL}?params=%7B%22ds0.session_id%22:%22{session_id}%22%7D"
                
                st.markdown(f"### [اضغط هنا لفتح لوحة القرار التنفيذي الخاصة بهذه الجلسة]({filtered_url})")
                st.components.v1.iframe(filtered_url, height=800, scrolling=True)
                
            except Exception as ex:
                st.error(f"خطأ أثناء الضخ: {str(ex)}")
