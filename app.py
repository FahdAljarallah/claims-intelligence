# منطق ضبط تصنيف السنوات التعاقدية الصارم (CY مقابل PY)
# شهور 12/2024 وما بعدها حتى 11/2025 تمثل السنة الحالية (CY)
# شهور 12/2023 حتى 11/2024 تمثل السنة السابقة (PY)

if std_month_code >= "2024-12":
    period_tag = "CY"
    year_label = "2024 / 2025"
elif std_month_code >= "2023-12" and std_month_code < "2024-12":
    period_tag = "PY"
    year_label = "2023 / 2024"
elif std_month_code >= "2025-12":
    period_tag = "CY"
    year_label = "2025 / 2026"
else:
    period_tag = "P2Y"
    year_label = "Prior Years"
