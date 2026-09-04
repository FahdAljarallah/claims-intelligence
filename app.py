import io
import re
import streamlit as st
import pandas as pd
import plotly.express as px
import pdfplumber

st.set_page_config(
    page_title="Corporate Claims Intelligence | لوحة تسعير وتفاوض التأمين الطبي",
    page_icon="🛡️",
    layout="wide"
)

# واجهة التفاوض والمعاملات
st.sidebar.header("⚙️ معايير التفاوض الاكتواري")
medical_trend = st.sidebar.slider(
    "نسبة التضخم الطبي المتوقعة (Medical Trend %):",
    min_value=5.0, max_value=20.0, value=10.0, step=0.5
) / 100.0

insurer_loading = st.sidebar.slider(
    "هامش التحميلات ومصاريف التأمين (Expense & Margin %):",
    min_value=15.0, max_value=30.0, value=22.0, step=1.0
) / 100.0

st.sidebar.markdown("---")
st.sidebar.info("🔒 الأمان والامتثال: تتم معالجة البيانات لحظياً داخل الجلسة ولا يتم حفظ أو تخزين أي سجلات علاجية للموظفين.")

# محرك قراءة الملفات
def parse_claims_file(uploaded_file):
    fname = uploaded_file.name.lower()
    monthly_rows = []
    metadata = {"Class": "غير محدد", "Deductible": "غير محدد", "Limit": "غير محدد"}

    if fname.endswith(('.xlsx', '.xls')):
        xls = pd.ExcelFile(uploaded_file)
        df_mc = pd.read_excel(xls, sheet_name='Monthly Claims', header=None)

        # استخراج الترويسة
        try:
            metadata["Class"] = str(df_mc.iloc[4, 1])
            metadata["Deductible"] = str(df_mc.iloc[5, 1])
            metadata["Limit"] = str(df_mc.iloc[6, 1])
        except Exception:
            pass

        # استخراج بيانات الأشهر والسنوات
        current_year = None
        for _, row in df_mc.iterrows():
            col0 = str(row[0]).strip()
            if "2 Years Prior" in col0:
                current_year = "PY-1 (قبل سنتين)"
            elif "Prior Policy Year" in col0:
                current_year = "PY (السنة السابقة)"
            elif "Last Policy Year" in col0 or "Current" in col0:
                current_year = "CY (السنة الحالية)"
            elif re.match(r'^\d{6}$', col0) and current_year:
                monthly_rows.append({
                    "Year": current_year,
                    "Month": col0,
                    "Lives": float(row[1]) if pd.notna(row[1]) else 0,
                    "Claims_Count": float(row[2]) if pd.notna(row[2]) else 0,
                    "Paid_SAR": float(row[3]) if pd.notna(row[3]) else 0
                })

    elif fname.endswith('.pdf'):
        with pdfplumber.open(uploaded_file) as pdf:
            text = "\n".join([p.extract_text() or "" for p in pdf.pages])
            current_year = "CY (السنة الحالية)"
            for line in text.split("\n"):
                match = re.search(r'(\d{6})\s+(\d+)\s+(\d+)\s+([\d,\.]+)', line)
                if match:
                    monthly_rows.append({
                        "Year": current_year,
                        "Month": match.group(1),
                        "Lives": float(match.group(2)),
                        "Claims_Count": float(match.group(3)),
                        "Paid_SAR": float(match.group(4).replace(',', ''))
                    })

    return metadata, pd.DataFrame(monthly_rows)

# واجهة المستخدم التنفيذية
st.title("🛡️ لوحة تسعير وتفاوض التأمين الطبي للشركات")
st.caption("أداة تحليل الأداء الفني لتقارير هيئة التأمين، عزل التضخم، واحتساب التسعير العادل للفئات.")

uploaded = st.file_uploader("اسحب تقرير تجربة المطالبات المعتمد (Excel أو PDF)", type=["xlsx", "xls", "pdf"])

if uploaded:
    metadata, df = parse_claims_file(uploaded)
    
    if df.empty:
        st.error("تعذر قراءة الجداول من الملف. تأكد من أن الملف مطابق لنموذج تقرير تجربة المطالبات المعتمد.")
    else:
        st.subheader("1. الملخص الفني ومعدل الحرق السنوي لكل رأس (Burning Rate)")
        
        # تجميع الأداء حسب السنة
        summary = []
        for yr in df['Year'].unique():
            sub = df[df['Year'] == yr]
            m_count = len(sub)
            tot_paid = sub['Paid_SAR'].sum()
            avg_lives = sub['Lives'].mean() if sub['Lives'].mean() > 0 else 1
            
            # تسوية سنوية لأشهر الرصد
            annualized_paid = (tot_paid / m_count) * 12 if m_count > 0 else tot_paid
            annualized_cpl = annualized_paid / avg_lives
            
            summary.append({
                "فترة الرصد": yr,
                "أشهر التقرير": m_count,
                "متوسط الأعضاء": int(avg_lives),
                "المطالبات المدفوعة (SAR)": tot_paid,
                "معدل الاستهلاك السنوي للفرد (SAR)": annualized_cpl
            })
            
        res_df = pd.DataFrame(summary)
        st.dataframe(res_df.style.format({
            "متوسط الأعضاء": "{:,}",
            "المطالبات المدفوعة (SAR)": "{:,.0f}",
            "معدل الاستهلاك السنوي للفرد (SAR)": "{:,.0f}"
        }), use_container_width=True)

        # حساب السعر الفني العادل للسنة القادمة (بناءً على أحدث فترة)
        latest_cpl = res_df.iloc[-1]["معدل الاستهلاك السنوي للفرد (SAR)"]
        latest_lives = res_df.iloc[-1]["متوسط الأعضاء"]
        
        pure_tech_rate = latest_cpl * (1 + medical_trend)
        fair_renewal_premium = pure_tech_rate / (1 - insurer_loading)
        projected_budget = fair_renewal_premium * latest_lives

        st.markdown("---")
        st.subheader("2. مصفوفة التفاوض والتسعير المقترح للتجديد (Renewal Matrix)")
        
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("تكلفة الفرد الفنية المتوقعة (بعد التضخم)", f"{pure_tech_rate:,.0f} SAR")
        with c2:
            st.metric("قسط التجديد العادل المقترح للفرد", f"{fair_renewal_premium:,.0f} SAR")
        with c3:
            st.metric("الميزانية التقديرية العادلة للوثيقة", f"{projected_budget:,.0f} SAR")

        # رسم بياني للاتجاه الشهري
        st.markdown("---")
        st.subheader("3. مسار المطالبات الشهرية")
        fig = px.bar(df, x="Month", y="Paid_SAR", color="Year", title="صافي المطالبات المدفوعة شهرياً (SAR)", text_auto='.2s')
        st.plotly_chart(fig, use_container_width=True)

        st.success(f"💡 نصيحة لغرفة التفاوض: إذا طلبت شركة التأمين سعراً يتجاوز {fair_renewal_premium:,.0f} ريال للفرد، فهذا يعني أن نسبة التحميل تتجاوز {insurer_loading*100:.0f}% أو أنهم يفترضون تضخماً طبياً غير مبرر.")
else:
    st.info("👆 يرجى رفع ملف التقرير لعرض المؤشرات ومحاكاة التسعير العادل.")
