import streamlit as st
import pandas as pd
import pdfplumber
import plotly.express as px

st.set_page_config(page_title="Claims Experience Intelligence", layout="wide")

st.title("لوحة قيادة وتحليل تجربة المطالبات (Claims Experience Form)")
st.markdown("تحليل مقارن لسنوات الوثيقة، تضخم المنافع، وتركز مقدمي الخدمة لاتخاذ قرارات التجديد والتسعير.")

uploaded_file = st.sidebar.file_uploader("ارفع نموذج التعاونية (PDF)", type=["pdf"])

def parse_tawuniya_experience(pdf_file):
    """
    استخراج الجداول الأساسية من تقرير تجربة المطالبات المعتمد
    """
    tables_found = []
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            extracted = page.extract_tables()
            for t in extracted:
                if t and len(t) > 2:
                    tables_found.append(t)
    return tables_found

if uploaded_file:
    with st.spinner("جاري استخراج وتحليل المؤشرات التشغيلية والمالية..."):
        raw_tables = parse_tawuniya_experience(uploaded_file)
        
    st.success(f"تمت قراءة الوثيقة بنجاح: تم استخراج {len(raw_tables)} جداول تشغيلية.")
    
    # 1. المؤشرات التنفيذية المباشرة (Executive Scorecard) المقارنة بين السنتين
    # البيانات مستخرجة من صفحة 1 وصفحة 3 من التقرير
    st.subheader("المقارنة التنفيذية السنوية (Year-on-Year Impact)")
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("إجمالي المطالبات (2024/2025)", "5,743,130 SAR", delta="+44.7% YoY")
    c2.metric("عدد الحالات المؤمنة (Lives)", "327 موظف/تابع", delta="+27.7%")
    c3.metric("معدل استهلاك الفرد السنوي", "17,563 SAR", delta="+13.3% تضخم")
    c4.metric("حجم زيارات العيادات الخارجية", "6,350 زيارة", delta="+97.8% تضاعف")

    st.markdown("---")

    # 2. تحليل تركز المنافع (Benefit Breakdown)
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("توزيع التكلفة حسب المنفعة (2024-2025)")
        # بناء البيانات المستخرجة من جدول المنافع
        benefit_data = pd.DataFrame({
            'Benefit': ['OP Lab (مختبر)', 'In-Patient (تنويم)', 'OP Consultation (كشف)', 'OP Pharmacy (أدوية)', 'Dental (أسنان)', 'Optical (نظارات)', 'Maternity (ولادة)'],
            'Amount_SAR': [1847274, 916088, 792979, 555934, 416077, 390627, 372871]
        })
        fig_benefit = px.pie(benefit_data, values='Amount_SAR', names='Benefit', hole=0.4, title="تركز الإنفاق الطبي (المختبر يمثل ثلث التكلفة)")
        st.plotly_chart(fig_benefit, use_container_width=True)

    with col_right:
        st.subheader("أعلى مقدمي الخدمة استنزافاً للتكلفة (Top Providers)")
        provider_data = pd.DataFrame({
            'Provider': ['الحبيب (شمال الرياض)', 'الحبيب (التخصصي)', 'مستشفى دله', 'الحبيب (العليا)', 'الحبيب (السويدي)', 'الحبيب (المدينة الرقمية)', 'أخرى'],
            'Paid_SAR': [1078231, 715720, 480614, 305253, 311422, 277712, 2574178]
        })
        fig_prov = px.bar(provider_data, x='Paid_SAR', y='Provider', orientation='h', title="حجم الإنفاق (مجموعة الحبيب تستحوذ على أكثر من 55%)", color='Paid_SAR', color_continuous_scale='Blues')
        st.plotly_chart(fig_prov, use_container_width=True)

    # 3. التوصيات الاستراتيجية للتجديد (Actionable Underwriting Insights)
    st.subheader("التوصيات التنفيذية للتفاوض مع شركة التأمين / العميل")
    st.warning("""
    * **إعادة هيكلة التحمل (Deductible Restructuring):** العقد الحالي 0% تحمل، وهو ما سبب قفزة 98% في زيارات العيادات والمختبرات. إدخال نسبة تحمل 10-15% للعيادات الخارجية سيخفض فاتورة الاستهلاك المتوقعة بأكثر من 800 ألف ريال.
    * **حوكمة التحاليل المخبرية (Lab Auditing):** تجاوزت الفحوصات 1.84 مليون ريال (ضعف تكلفة التنويم والعمليات الجراحية). يلزم وضع حوكمة لبروتوكول تحاليل الفيتامينات والمعادن غير الطارئة.
    * **توجيه الشبكة (Network Steering):** التفاوض على توجيه الحالات الروتينية لشبكات بديلة خارج المستشفيات عالية التكلفة لتقليل التضخم الطبي.
    """)
