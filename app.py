import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import json
import urllib.parse
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

# 1. تهيئة الصفحة
st.set_page_config(
    page_title="Claims Intelligence Portal",
    page_icon="📊",
    layout="wide"
)

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"
LOOKER_REPORT_URL = "https://lookerstudio.google.com/reporting/34329d81-4adf-410e-86a9-24713511ec47/page/1f97F"

# 2. إدارة الاتصال بـ BigQuery مع تصحيح مفتاح التشفير آلياً
@st.cache_resource
def get_bq_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    
    if "private_key" in creds_dict:
        pk = creds_dict["private_key"].replace("\\n", "\n")
        if "-----BEGIN PRIVATE KEY-----" not in pk:
            clean_body = pk.replace("-----BEGIN PRIVATE KEY-----", "").replace("-----END PRIVATE KEY-----", "").strip()
            if clean_body.startswith("nMI"):
                clean_body = clean_body[1:]
            pk = f"-----BEGIN PRIVATE KEY-----\n{clean_body}\n-----END PRIVATE KEY-----\n"
        creds_dict["private_key"] = pk

    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)

def append_to_bigquery(df_monthly, df_benefits, df_providers):
    client = get_bq_client()
    
    # التحقق وضخ البيانات للجداول التي تحتوي على سجلات فعلية فقط
    if not df_monthly.empty:
        records_m = df_monthly.to_dict(orient="records")
        err_m = client.insert_rows_json(f"{PROJECT_ID}.{DATASET_ID}.monthly_performance", records_m)
        if err_m:
            raise RuntimeError(f"خطأ في ضخ بيانات الأداء الشهري: {err_m}")
            
    if not df_benefits.empty:
        records_b = df_benefits.to_dict(orient="records")
        err_b = client.insert_rows_json(f"{PROJECT_ID}.{DATASET_ID}.benefits_breakdown", records_b)
        if err_b:
            raise RuntimeError(f"خطأ في ضخ تفاصيل المنافع: {err_b}")
            
    if not df_providers.empty:
        records_p = df_providers.to_dict(orient="records")
        err_p = client.insert_rows_json(f"{PROJECT_ID}.{DATASET_ID}.top_providers", records_p)
        if err_p:
            raise RuntimeError(f"خطأ في ضخ مقدمي الخدمة: {err_p}")

def delete_session_data(target_session_id):
    client = get_bq_client()
    tables = ["monthly_performance", "benefits_breakdown", "top_providers"]
    for t in tables:
        query = f"DELETE FROM `{PROJECT_ID}.{DATASET_ID}.{t}` WHERE session_id = @sid"
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", target_session_id)]
        )
        client.query(query, job_config=job_config).result()

# 3. إعداد مصفوفة النصوص ثنائية اللغة
i18n = {
    "AR": {
        "title": "مرصد المطالبات ومحاكاة التجديد | Claims Intelligence",
        "subtitle": "قم برفع ملف تجربة المطالبات لقراءة الأداء وتحديث لوحة المؤشرات فوراً.",
        "lang_label": "اللغة / Language",
        "date_label": "تاريخ بداية سريان الوثيقة",
        "prem_label": "قسط الوثيقة السنوي الحالي (SAR)",
        "members_label": "إجمالي عدد المؤمن عليهم (Lives)",
        "upload_label": "رفع ملف تجربة المطالبات (Excel أو CSV)",
        "btn_process": "قراءة وتحليل البيانات",
        "processing": "جاري قراءة البيانات وتجهيز المؤشرات...",
        "success": "تم تجهيز البيانات بنجاح للجلسة: ",
        "btn_open_looker": "الانتقال المباشر إلى لوحة المؤشرات في Looker Studio",
        "warn_inputs": "يرجى تعبئة قسط الوثيقة، عدد الأفراد، وتاريخ السريان.",
        "session_mgmt": "إدارة وحوكمة الجلسة",
        "btn_end_session": "إنهاء الجلسة وحذف البيانات",
        "session_cleared": "تم حذف بيانات الجلسة بنجاح وتطهير السجلات."
    },
    "EN": {
        "title": "Claims Intelligence & Renewal Dashboard",
        "subtitle": "Upload policy claims experience to analyze performance and update metrics.",
        "lang_label": "Language / اللغة",
        "date_label": "Policy Inception Date",
        "prem_label": "Current Annual Premium (SAR)",
        "members_label": "Total Covered Members (Lives)",
        "upload_label": "Upload Claims Experience (Excel or CSV)",
        "btn_process": "Process Data",
        "processing": "Reading data and preparing metrics...",
        "success": "Data processed successfully for session: ",
        "btn_open_looker": "Open Dashboard in Looker Studio",
        "warn_inputs": "Please enter current premium, covered members, and inception date.",
        "session_mgmt": "Session Governance",
        "btn_end_session": "End Session & Delete Data",
        "session_cleared": "Session data deleted and purged successfully."
    }
}

# اختيار لغة الواجهة
selected_lang = st.selectbox("Language / اللغة", options=["العربية", "English"], index=0)
lang_code = "AR" if selected_lang == "العربية" else "EN"
t = i18n[lang_code]

st.title(t["title"])
st.markdown(t["subtitle"])

# 4. الحقول التنفيذية فارغة مسبقاً لمنع الأخطاء
col_date, col_members = st.columns(2)
with col_date:
    inception_date = st.date_input(t["date_label"], value=None)

with col_members:
    total_members = st.number_input(
        t["members_label"], 
        min_value=1, 
        max_value=1000000, 
        value=None, 
        step=1,
        placeholder="مثال: 1,250"
    )

col_prem, _ = st.columns(2)
with col_prem:
    current_premium = st.number_input(
        t["prem_label"], 
        min_value=1000.0, 
        max_value=500000000.0, 
        value=None, 
        step=50000.0,
        format="%.2f",
        placeholder="مثال: 4,500,000.00"
    )

if current_premium:
    st.caption(f"SAR {current_premium:,.2f}")

uploaded_file = st.file_uploader(t["upload_label"], type=["xlsx", "xls", "csv"])

# 5. منطق القراءة والضخ وتوليد الرابط
if uploaded_file:
    if st.button(t["btn_process"]):
        if not current_premium or not total_members or not inception_date:
            st.warning(t["warn_inputs"])
        else:
            with st.spinner(t["processing"]):
                try:
                    session_id = f"session_{uuid.uuid4().hex[:8]}"
                    st.session_state["active_session_id"] = session_id
                    now_iso = datetime.utcnow().isoformat()

                    # قراءة متكيفة لملف الإكسل لضمان عدم تمرير جداول فارغة
                    if uploaded_file.name.endswith(('xlsx', 'xls')):
                        excel_dict = pd.read_excel(uploaded_file, sheet_name=None)
                        sheet_names = list(excel_dict.keys())
                        
                        # مطابقة أسماء الأوراق أو أخذ الورقة الأولى كبيانات شهرية أساسية
                        df_m = excel_dict.get('Monthly', excel_dict.get('monthly_performance', excel_dict[sheet_names[0]]))
                        df_b = excel_dict.get('Benefits', excel_dict.get('benefits_breakdown', pd.DataFrame()))
                        df_p = excel_dict.get('Providers', excel_dict.get('top_providers', pd.DataFrame()))
                    else:
                        df_raw = pd.read_csv(uploaded_file)
                        df_m, df_b, df_p = df_raw, pd.DataFrame(), pd.DataFrame()

                    # تطعيم البيانات بمعرف الجلسة وتوقيت الإنشاء
                    for df in [df_m, df_b, df_p]:
                        if not df.empty:
                            df['session_id'] = session_id
                            df['created_at'] = now_iso

                    # تنفيذ الضخ في مستودع البيانات
                    append_to_bigquery(df_m, df_b, df_p)

                    # مطابقة أسماء الـ Variables مع Looker Studio (ds14, ds15, ds16)
                    url_params = {
                        "ds14.p_session_id": session_id,
                        "ds15.p_session_id": session_id,
                        "ds16.p_session_id": session_id,
                        "ds14.param_language": lang_code,
                        "ds14.p_current_premium": int(current_premium),
                        "ds14.p_target_census": int(total_members)
                    }

                    encoded_params = urllib.parse.urlencode({"params": json.dumps(url_params)})
                    target_url = f"{LOOKER_REPORT_URL}?{encoded_params}"

                    st.success(f"{t['success']} `{session_id}`")
                    st.link_button(label=t["btn_open_looker"], url=target_url, type="primary")

                except Exception as e:
                    st.error(f"حدث خطأ أثناء معالجة وضخ الملف: {str(e)}")

# 6. قسم الحوكمة وحذف البيانات المؤقتة
if "active_session_id" in st.session_state:
    st.divider()
    st.subheader(t["session_mgmt"])
    col_s1, col_s2 = st.columns([3, 1])
    with col_s1:
        st.info(f"الجلسة النشطة الحالية: `{st.session_state['active_session_id']}`")
    with col_s2:
        if st.button(t["btn_end_session"], type="secondary"):
            with st.spinner("جاري حذف البيانات..."):
                try:
                    delete_session_data(st.session_state["active_session_id"])
                    del st.session_state["active_session_id"]
                    st.success(t["session_cleared"])
                    st.rerun()
                except Exception as ex:
                    st.error(f"تعذر حذف البيانات: {str(ex)}")
