import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Executive Renewal & Claims Intelligence", layout="wide")

st.title("محرك القرارات التنفيذية لتجديد التأمين الطبي | Renewal & Underwriting Engine")
st.markdown("تشخيص التضخم المالي، كشف الهدر الاستهلاكي، ومحاكاة قرارات إعادة هيكلة المنافع.")

# 1. المؤشرات التنفيذية المحورية (استناداً إلى بيانات الوثيقة المرفقة)
# السنة السابقة (2023-2024) مقابل السنة الحالية (2024-2025)
st.subheader("1. بطاقة التشخيص المالي السنوي (Financial Health Scorecard)")

col1, col2, col3, col4 = st.columns(4)
col1.metric("إجمالي المطالبات المدفوعة", "5.74M SAR", "+44.7% ارتفاع")
col2.metric("تكلفة التغطية للموظف الواحد", "17,563 SAR/سنة", "+13.3% تضخم")
col3.metric("معدل التردد على العيادات (OP)", "19.4 زيارة/فرد", "+55% استهلاك مفرط")
col4.metric("حصة المختبرات من التكلفة", "32.1%", "1.84M SAR")

st.markdown("---")

# 2. تفكيك نزيف التكلفة (Cost Breakdown & Provider Leakage)
c_left, c_right = st.columns(2)

with c_left:
    st.subheader("أين تذهب الميزانية؟ (حسب نوع المنفعة)")
    benefit_df = pd.DataFrame({
        'المنفعة': ['مختبرات خارجية (Lab)', 'تنويم وعمليات (IP)', 'استشارات عيادات (Consultation)', 'أدوية وصيدليات (Pharmacy)', 'أسنان (Dental)', 'بصريات (Optical)', 'أمومة وولادة (Maternity)'],
        'المطالبات (SAR)': [1847274, 916088, 792979, 555934, 416077, 390627, 372871]
    })
    fig_b = px.pie(benefit_df, values='المطالبات (SAR)', names='المنفعة', hole=0.45, color_discrete_sequence=px.colors.sequential.Teal)
    st.plotly_chart(fig_b, use_container_width=True)

with c_right:
    st.subheader("تركز مقدمي الخدمة (Provider Concentration)")
    provider_df = pd.DataFrame({
        'مقدم الخدمة': ['الحبيب (شمال الرياض)', 'الحبيب (التخصصي)', 'مستشفى دله', 'الحبيب (السويدي)', 'الحبيب (العليا)', 'الحبيب (المدينة الرقمية)', 'أخرى'],
        'المبلغ (SAR)': [1078231, 715720, 480614, 311422, 305253, 277712, 2574178]
    })
    fig_p = px.bar(provider_df, x='المبلغ (SAR)', y='مقدم الخدمة', orientation='h', color='المبلغ (SAR)', color_continuous_scale='Reds')
    st.plotly_chart(fig_p, use_container_width=True)

st.markdown("---")

# 3. محاكي قرارات التجديد (Executive Renewal Scenario Simulator)
st.subheader("2. محاكي الأثر المالي للتفاوض وإعادة تصميم المنافع (ROI Simulator)")
st.info("حساب العائد المالي الفوري لتغيير شروط الوثيقة لكبح التضخم قبل توقيع العقد الجديد:")

sim_col1, sim_col2 = st.columns(2)

with sim_col1:
    copay_rate = st.slider("تعديل نسبة التحمل للعيادات الخارجية والمختبرات (Copay %)", min_value=0, max_value=25, value=15, step=5)
    lab_audit_tightening = st.checkbox("تفعيل الموافقة المسبقة وحوكمة تحاليل المختبر غير الطارئة", value=True)

with sim_col2:
    # احتساب الوفر المتوقع:
    # إجمالي مطالبات العيادات الخارجية والمختبرات = 4,827,042 ريال
    op_spend = 4827042
    # أثر التحمل: مشاركة مالية + كبح الزيارات غير الضرورية (Utilization drop) بنسبة ثلث نسبة التحمل تقريباً
    copay_savings = op_spend * (copay_rate / 100.0) * 1.35
    lab_savings = 1847274 * 0.18 if lab_audit_tightening else 0
    total_savings = copay_savings + lab_savings

    st.metric("إجمالي الوفر المالي المتوقع في فاتورة التجديد", f"{total_savings:,.0f} SAR")
    st.success(f"النتيجة التنفيذية: تطبيق هذه التعديلات يمنحك خفضاً تفاوضياً يعادل **{ (total_savings / 5743130) * 100:.1f}%** من إجمالي القسط المعروض.")

st.markdown("---")
st.subheader("3. ورقة التوصيات التنفيذية للتفاوض (Executive Brief)")
st.markdown("""
* **شرط التحمل (Copay Mandate):** إلغاء الـ 0% Copay فوراً وإقرار 15% بحد أقصى 50 ريال. هذا القرار التشغيلي وحده سيخفض تردد العيادات غير المبرر بنسبة تفوق 15%.
* **حوكمة بند المختبرات:** إلزام شركة التأمين/المستشفى بعدم تمرير باقات تحاليل الفيتامينات والتحاليل الشاملة الروتينية إلا بموافقة طبية مبررة.
* **توزيع الحالات:** توجيه الاستشارات الروتينية للطب الاتصالي (Telehealth) والمراكز الأولية بدلاً من الطوارئ ومستشفيات النخبة.
""")
