import streamlit as st
import pandas as pd
import pdfplumber

st.sidebar.header("تحميل البيانات التشغيلية")
uploaded_file = st.sidebar.file_uploader(
    "ارفع تقرير المطالبات (PDF أو Excel/CSV)", 
    type=["pdf", "xlsx", "csv"]
)

@st.cache_data
def process_claims_pdf(file):
    """
    استخراج الجداول المنتظمة من تقارير المطالبات بصيغة PDF
    وتحويلها إلى هيكل بيانات تحليلي
    """
    all_data = []
    with pdfplumber.open(file) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    # تصفية الصفوف الفارغة أو فواصل الصفحات
                    cleaned_row = [cell.strip() if isinstance(cell, str) else cell for cell in row]
                    if any(cleaned_row):
                        all_data.append(cleaned_row)
    
    if not all_data:
        return pd.DataFrame()

    # السطر الأول عادة يمثل العناوين
    headers = all_data[0]
    df = pd.DataFrame(all_data[1:], columns=headers)
    
    # تنظيف مالي وتشغيلي أولي (إزالة الفواصل وتحويل المبالغ لأرقام)
    # ملاحظة: تعديل أسماء الأعمدة أدناه يتم وفق تسميات التقرير الفعلي
    return df

# آلية سحب البيانات بالتطبيق
if uploaded_file:
    if uploaded_file.name.endswith(".pdf"):
        df_raw = process_claims_pdf(uploaded_file)
    elif uploaded_file.name.endswith(".csv"):
        df_raw = pd.read_csv(uploaded_file)
    else:
        df_raw = pd.read_excel(uploaded_file)
        
    st.success(f"تم تحميل ومعالجة التقرير بنجاح: {len(df_raw)} سجل مطالبة.")
else:
    st.info("يرجى تحميل تقرير المطالبات لتحديث المؤشرات التنفيذية.")
