import asyncio, asyncpg, json

async def main():
    conn = await asyncpg.connect(host='db', port=5432, user='postgres', password='postgres', database='invoice_db')
    rows = await conn.fetch("""
        SELECT id, batch_id, file_name, processing_status, counterparty, amount,
               trip_origin, trip_destination, trip_time_text,
               extracted_fields_json, updated_at
        FROM source_documents
        WHERE file_name LIKE '%高德打车电子行程单.pdf'
        ORDER BY batch_id DESC, id
        LIMIT 30
    """)
    await conn.close()
    for r in rows:
        raw = r['extracted_fields_json']
        if isinstance(raw, str):
            raw = json.loads(raw)
        v = (raw or {}).get('_validation', {})
        print('---', r['id'], 'batch', r['batch_id'], r['updated_at'])
        print(r['file_name'])
        print('status', r['processing_status'], 'provider', r['counterparty'], 'amount', r['amount'])
        print('origin', r['trip_origin'])
        print('dest', r['trip_destination'])
        print('time', r['trip_time_text'])
        print('final_source', v.get('final_source'), 'path', v.get('escalation_path'), 'prompt', v.get('prompt_version'))

asyncio.run(main())
