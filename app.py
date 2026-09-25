df_temp_m = pd.DataFrame(monthly_rows)
    if not df_temp_m.empty and 'month_code' in df_temp_m.columns:
        # استبعاد صف أعداد المستفيدين عند حساب مدة العقد الفعلية
        file_contract_durations = {}
        for file_name_key, group_df in df_temp_m.groupby('table_header'):
            valid_calendar_months = sorted(list({
                m for m in group_df['month_code'].unique() 
                if m and isinstance(m, str) and re.match(r'^\d{4}-\d{2}$', m.strip())
            }))
            
            # إذا كانت الشهور الفعلية للفعاليات تفوق 12 شهراً، فهو 13، وإلا 12 شهراً قياسياً
            if len(valid_calendar_months) > 12:
                file_contract_durations[file_name_key] = "13 Months"
            else:
                file_contract_durations[file_name_key] = "12 Months"
                
        df_temp_m['contract_period'] = df_temp_m['table_header'].map(file_contract_durations)
        
        df_temp_m = df_temp_m.sort_values(by=['table_header', 'class_tier', 'month_code'])
        ranks_list = []
        rank_counter_map = {}
        
        for _, r in df_temp_m.iterrows():
            if r['month_code'] == 'Number of lives at start':
                ranks_list.append("First")
                continue
                
            key = (r['table_header'], r['class_tier'])
            if key not in rank_counter_map:
                rank_counter_map[key] = 0
            
            rank_counter_map[key] += 1
            curr_item_index = rank_counter_map[key]
            
            period_len = 13 if r['contract_period'] == "13 Months" else 12
            group_index = ((curr_item_index - 1) // period_len) + 1
            
            if group_index == 1: ranks_list.append("First")
            elif group_index == 2: ranks_list.append("2nd")
            elif group_index == 3: ranks_list.append("3rd")
            else: ranks_list.append(f"{group_index}th")
                
        df_temp_m['contract_rank'] = ranks_list
        monthly_rows = df_temp_m.to_dict(orient='records')
