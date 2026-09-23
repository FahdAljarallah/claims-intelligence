# 2. القسم الثاني: Breakdown by Benefit (تصحيح وتثبيت أسماء المنافع بدقة تامة)
        elif current_section == "benefit":
            cleaned_row_text = row_text
            if current_class_tier and current_class_tier in cleaned_row_text:
                cleaned_row_text = cleaned_row_text.replace(current_class_tier, "").strip()
            
            line_lower_clean = cleaned_row_text.lower()
            nums = [clean_number(t) for t in row_tokens if re.search(r'\d', t)]
            
            if nums and len(row_text) > 4:
                benefit_label = None
                
                if "out" in line_lower_clean or "out-patient" in line_lower_clean or ("basic" in line_lower_clean and "out" in line_lower_clean):
                    benefit_label = "Basic Coverage (Out-Patient)"
                elif "in" in line_lower_clean or "in-patient" in line_lower_clean or ("basic" in line_lower_clean and "in" in line_lower_clean):
                    benefit_label = "Basic Coverage (In-Patient)"
                elif "dental" in line_lower_clean:
                    benefit_label = "Dental"
                elif "optical" in line_lower_clean:
                    benefit_label = "Optical"
                elif "mat" in line_lower_clean or "maternity" in line_lower_clean:
                    benefit_label = "Maternity"
                elif "lab" in line_lower_clean:
                    benefit_label = "Lab"
                elif "consulation" in line_lower_clean or "consultation" in line_lower_clean:
                    benefit_label = "Consultation"
                elif "pharmacy" in line_lower_clean or "med" in line_lower_clean:
                    benefit_label = "Pharama/Med"
                elif "total" in line_lower_clean or "الإجمالي" in line_lower_clean:
                    benefit_label = "Total"
                else:
                    benefit_label = "General Benefit"

                benefit_rows.append({
                    "created_at": created_at_ts,
                    "policy_year": current_policy_year or "LAST POLICY YEAR",
                    "table_header": file_name,
                    "month_code": "Benefit Summary",
                    "class_tier": current_class_tier or "CLASS GENERAL",
                    "active_lives": 0,
                    "claims_count": int(nums[0]) if len(nums) > 5 else 0,
                    "paid_claims_sar": float(nums[1]) if len(nums) > 5 else float(nums[0]),
                    "paid_claims_vat_sar": float(nums[2]) if len(nums) > 5 else 0.0,
                    "OS_claims_count": int(nums[3]) if len(nums) > 5 else 0,
                    "OS paid_claims_sar": float(nums[4]) if len(nums) > 4 else 0.0,
                    "OS paid_claims_vat_sar": float(nums[5]) if len(nums) > 5 else 0.0,
                    "section_type": "Breakdown by Benefit",
                    "benefit_name": benefit_label
                })
