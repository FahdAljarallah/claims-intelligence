import io
import re
import uuid
import urllib.parse
import streamlit as st
import pandas as pd
import pdfplumber

st.set_page_config(
    page_title="Claims Experience Ingestor",
    page_icon="🛡️",
    layout="centered"
)

# رابط لوحة Looker Studio الأساسية (يتم استبدال المعرف برابط لوحتك الفعلي)
LOOKER_STUDIO_BASE_URL = "https://lookerstudio.google.com/reporting/YOUR_REPORT_ID/page/YOUR_PAGE_ID"

def generate_session_id():
    return f"SES_{uuid.uuid4().hex[:8].upper()}"

def parse_claims_report(uploaded_file, session_id):
    fname = uploaded_file.name.lower()
    monthly_rows = []
    benefits_rows = []
    providers_rows = []
    
    if fname.endswith(('.xlsx', '.xls')):
        xls = pd.ExcelFile(uploaded_file)
        
        # 1. قراءة بيانات الأداء الشهري (Monthly Claims)
        df_mc = pd.read_excel(xls, sheet_name='Monthly Claims', header=None)
        class_tier = str(df_mc.iloc[5, 1]) if pd.notna(df_mc.iloc[5, 1]) else "Class A"
        
        curr_year = None
        for _, row in df_mc.iterrows():
            val0 = str(row[0]).strip()
            if "2 Years Prior" in val0:
                curr_year = "PY-1"
                continue
            elif "Prior Policy Year" in val0:
                curr_year = "PY"
                continue
            elif "Last Policy Year" in val0 or "Current" in val0:
                curr_year = "CY"
                continue
            
            if re.match(r'^\d{6}$', val0) and curr_year:
                monthly_rows.append({
                    'session_id': session_id,
                    'class_tier': class_tier,
                    'policy_year': curr_year,
                    'month_code': val0,
                    'active_lives': float(row[1]) if pd.notna(row[1]) else 0.0,
                    'claims_count': float(row[2]) if pd.notna(row[2]) else 0.0,
                    'paid_claims_sar': float(row[3]) if pd.notna(row[3]) else 0.0,
                    'paid_claims_vat_sar': float(row[4]) if pd.notna(row[4]) else 0.0
                })
        
        # 2. قراءة تشريح المنافع (Breakdown by Benefit)
        df_bb = pd.read_excel(xls, sheet_name='Breakdown by Benefit', header=None)
        curr_year = None
        for _, row in df_bb.iterrows():
            val0 = str(row[0]).strip()
            if "2 Years Prior" in val0:
                curr_year = "PY-1"
                continue
            elif "Prior Policy Year" in val0:
                curr_year = "PY"
                continue
            elif "Last Policy Year" in val0 or "Current" in val0:
                curr_year = "CY"
                continue
            
            if re.match(r'^\d*\.?[A-Za-z]', val0) and curr_year and "Overall" not in val0:
                benefits_rows.append({
                    'session_id': session_id,
                    'class_tier': class_tier,
                    'policy_year': curr_year,
                    'benefit_name': val0,
                    'claims_count': float(row[1]) if pd.notna(row[1]) else 0.0,
                    'paid_claims_sar': float(row[2]) if pd.notna(row[2]) else 0.0,
                    'paid_claims_vat_sar': float(row[3]) if pd.notna(row[3]) else 0.0
                })

        # 3. قراءة مقدمي الخدمة (Top Providers)
        df_tp = pd.read_excel(xls, sheet_name='Top Providers', header=None)
        curr_year = None
        for _, row in df_tp.iterrows():
            val0 = str(row[0]).strip()
            if "2 Years Prior" in val0:
                curr_year = "PY-1"
                continue
            elif "Prior Policy Year" in val0:
                curr_year = "PY"
                continue
            elif "Lasr Policy Year" in val0 or "Last Policy Year" in val0:
                curr_year = "CY"
                continue
            
            if re.match(r'^\d+$', val0) and curr_year:
                providers_rows.append({
                    'session_id': session_id,
                    'class_tier': class_tier,
                    'policy_year': curr_year,
                    'rank': int(val0),
                    'provider_name': str(row[1]).strip(),
                    'claims_count': float(row[2]) if pd.notna(row[2]) else 0.0,
                    'paid_claims_sar': float(row[3]) if pd.notna(row[3]) else 0.0,
                    'paid_claims_vat_sar': float(row[4]) if pd.notna(row[4]) else 0.0
                })

    elif fname.endswith('.pdf'):
        # معالجة ملف الـ PDF عبر pdfplumber واستخراج الأرقام لنفس الحقول
        with pdfplumber.open(uploaded_file) as pdf:
            text = "\n".join([page.extract_text() or "" for page in pdf.pages])
            curr_year = "CY"
            for line in text.split("\n"):
                match = re.search(r'(\d{6})\s+(\d+)\s+(\d+)\s+([\d,\.]+)\s+([\d,\.]+)', line)
                if match:
                    monthly_rows.append({
                        'session_id': session_id,
                        'class_tier': "Class A",
                        'policy_year': curr_year,
                        'month_code': match.group(1),
                        'active_lives': float(match.group(2)),
                        'claims_count': float(match.group(3)),
                        'paid_claims_sar': float(match.group(4).replace(',', '')),
                        'paid_claims_vat_sar': float(match.group(5).replace(',', ''))
                    })

    return (
        pd.DataFrame(monthly_rows),
        pd.DataFrame(benefits_rows),
        pd.DataFrame(providers_rows)
    )

# واجهة الاستيعاب والرفع (المضمنة داخل Looker)
st.markdown("### 📤 رفع تقرير تجربة المطالبات (Claims Experience)")
st.caption("الأنظمة المدعومة: تقارير هيئة التأمين بصيغة Excel أو PDF. المعالجة معزولة ومحمية بالكامل.")

uploaded = st.file_uploader("اختر ملف التقرير للبدء بالتحليل اللحظي:", type=["xlsx", "xls", "pdf"])

if uploaded:
    session_id = generate_session_id()
    with st.spinner("جارٍ تفكيك وتسطيح البيانات وعزل الجلسة..."):
        df_monthly, df_benefits, df_providers = parse_claims_report(uploaded, session_id)
        
        if df_monthly.empty:
            st.error("تعذر التعرف على جداول التقرير. يرجى التأكد من رفع النموذج المعتمد.")
        else:
            # تجهيز رابط Looker Studio الديناميكي المفلتر بالـ session_id
            params = {"ds0.session_id": session_id}
            encoded_params = urllib.parse.quote(str(params).replace("'", '"'))
            looker_url = f"{LOOKER_STUDIO_BASE_URL}?params={encoded_params}"
            
            st.success(f"تمت معالجة البيانات بنجاح! رمز جلستك المعزولة: **{session_id}**")
            
            # زر نقل المستخدم مباشرة إلى اللوحة المخصصة
            st.link_button("🚀 فتح لوحة التحليل والتفاوض الخاصة بك", looker_url, type="primary")
            
            # تنزيل الجداول المسطحة (كخيار احتياطي ومساند)
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                df_monthly.to_excel(writer, sheet_name='Monthly_Performance', index=False)
                df_benefits.to_excel(writer, sheet_name='Benefits_Breakdown', index=False)
                df_providers.to_excel(writer, sheet_name='Top_Providers', index=False)
            
            st.download_button(
                label="📥 تحميل البيانات المسطحة النظيفة (Excel)",
                data=excel_buffer.getvalue(),
                file_name=f"Clean_Claims_{session_id}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
