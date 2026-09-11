import streamlit as st
import pandas as pd
import pdfplumber

st.set_page_config(page_title="Universal Claims Raw Ingestion", layout="wide")

st.title("محرك السحب الشامل لبيانات المطالبات (Raw Extractor)")
st.caption("سحب كافة الجداول والبيانات من ملفات PDF و Excel بالكامل دون تصفية مسبقة.")

uploaded_file = st.sidebar.file_uploader("ارفع ملف المطالبات (PDF أو Excel)", type=["pdf", "xlsx", "xls"])

def extract_all_from_pdf(file):
    """سحب جميع الجداول من كافة صفحات الـ PDF بلا استثناء"""
    extracted_tables = {}
    with pdfplumber.open(file) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            for table_idx, table in enumerate(tables):
                if table and len(table) > 1:
                    # تنظيف الخلايا
                    cleaned_table = [
                        [str(cell).strip().replace('\n', ' ') if cell is not None else '' for cell in row]
                        for row in table
                    ]
                    # اعتبار أول سطر عنوان
                    headers = cleaned_table[0]
                    # معالجة تكرار العناوين الفارغة
                    headers = [h if h else f"Col_{i+1}" for i, h in enumerate(headers)]
                    data = cleaned_table[1:]
                    df = pd.DataFrame(data, columns=headers)
                    # تصفية الصفوف الفارغة بالكامل
                    df = df.dropna(how='all')
                    key_name = f"الصفحة {page_idx + 1} - جدول {table_idx + 1}"
                    extracted_tables[key_name] = df
    return extracted_tables

def extract_all_from_excel(file):
    """سحب كافة الشيتات من ملف الإكسل بالكامل"""
    extracted_sheets = {}
    xls = pd.ExcelFile(file)
    for sheet_name in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet_name)
        extracted_sheets[f"ورقة: {sheet_name}"] = df
    return extracted_sheets

if uploaded_file:
    file_ext = uploaded_file.name.split('.')[-1].lower()
    
    with st.spinner("جاري سحب كافة الجداول والبيانات من الملف..."):
        if file_ext == "pdf":
            raw_data_dict = extract_all_from_pdf(uploaded_file)
        else:
            raw_data_dict = extract_all_from_excel(uploaded_file)

    if raw_data_dict:
        st.success(f"تم سحب {len(raw_data_dict)} جدول/ورقة بيانات بالكامل بنجاح!")
        
        # استعراض كل الجداول المسحوبة في تابات منفصلة
        tabs = st.tabs(list(raw_data_dict.keys()))
        for idx, (table_name, df) in enumerate(raw_data_dict.items()):
            with tabs[idx]:
                st.subheader(f"بيانات: {table_name}")
                st.write(f"الأبعاد: {df.shape[0]} صفوف × {df.shape[1]} أعمدة")
                st.dataframe(df, use_container_width=True)
                
                # إتاحة التنزيل المؤقت للمعاينة
                csv_data = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label=f"تحميل {table_name} كـ CSV",
                    data=csv_data,
                    file_name=f"{table_name}.csv",
                    mime="text/csv",
                    key=f"dl_{idx}"
                )
    else:
        st.warning("لم يتم العثور على جداول داخل الملف.")
else:
    st.info("يرجى رفع ملف الـ PDF أو Excel لبدء السحب الشامل.")
