# واجهة المستخدم التنفيذية
st.title("مرصد المطالبات ومحاكاة التجديد الاكتواري | Claims Intelligence")
st.markdown("قم برفع ملف تجربة المطالبات لضخ البيانات وتحديث لوحة المؤشرات التفاوضية فوراً.")

# الصف الأول: إعدادات العرض واللغة
col_lang, col_date = st.columns(2)
with col_lang:
    selected_language = st.selectbox(
        "لغة عرض اللوحة في Looker Studio",
        options=["العربية", "English"],
        index=0
    )
    lang_param = "AR" if selected_language == "العربية" else "EN"

with col_date:
    inception_date = st.date_input("تاريخ بداية سريان الوثيقة", value=datetime(2025, 1, 1))

# الصف الثاني: الركائز المالية للمحفظة
col_prem, col_members = st.columns(2)
with col_prem:
    current_premium = st.number_input(
        "قسط الوثيقة السنوي الحالي (SAR)", 
        min_value=100000.0, 
        value=5000000.0, 
        step=50000.0,
        format="%.2f"
    )
with col_members:
    total_members = st.number_input(
        "إجمالي عدد المؤمن عليهم (Lives)", 
        min_value=1, 
        value=1250, 
        step=10
    )

uploaded_file = st.file_uploader("رفع ملف تجربة المطالبات (Excel أو CSV)", type=["xlsx", "xls", "csv"])
