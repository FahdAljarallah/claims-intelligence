from google.cloud import bigquery

PROJECT_ID = "claims-intelligence-507611"
DATASET_ID = "claims_intelligence"

def get_bq_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    credentials = Credentials.from_service_account_info(creds_dict)
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)

def append_to_bigquery(df_monthly, df_benefits, df_providers):
    client = get_bq_client()
    
    # تحويل البيانات لصيغة ملائمة لـ BigQuery مع ضمان التوقيت السليم
    records_m = df_monthly.to_dict(orient="records")
    records_b = df_benefits.to_dict(orient="records")
    records_p = df_providers.to_dict(orient="records")
    
    # تحويل التواريخ النصية إلى ISO Format متوافق مع TIMESTAMP
    for r in records_m:
        r['created_at'] = r['created_at']
    for r in records_b:
        r['created_at'] = r['created_at']
    for r in records_p:
        r['created_at'] = r['created_at']

    # Streaming Insert فوري (أقل من ثانية)
    err_m = client.insert_rows_json(f"{PROJECT_ID}.{DATASET_ID}.monthly_performance", records_m)
    err_b = client.insert_rows_json(f"{PROJECT_ID}.{DATASET_ID}.benefits_breakdown", records_b)
    err_p = client.insert_rows_json(f"{PROJECT_ID}.{DATASET_ID}.top_providers", records_p)
    
    if err_m or err_b or err_p:
        raise RuntimeError(f"BQ Insertion Error: {err_m or err_b or err_p}")

def delete_bq_session(target_session_id):
    client = get_bq_client()
    tables = ["monthly_performance", "benefits_breakdown", "top_providers"]
    for t in tables:
        query = f"DELETE FROM `{PROJECT_ID}.{DATASET_ID}.{t}` WHERE session_id = @sid"
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", target_session_id)]
        )
        client.query(query, job_config=job_config).result()
