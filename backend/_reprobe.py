import asyncio, asyncpg, pdfplumber
from io import BytesIO
from app.services.document_service import (
    _extract_itinerary_by_rules, _map_public_fields, _rule_extraction_acceptable,
    _parse_gaode_layout_rows
)
from app.models.batch import DocumentType
from app.services.ocr_service import get_ocr_service

async def main():
    conn = await asyncpg.connect(host='db', port=5432, user='postgres', password='postgres', database='invoice_db')
    rows = await conn.fetch("""
        SELECT id, file_name, file_data, raw_text, trip_origin, trip_destination, trip_time_text
        FROM source_documents
        WHERE batch_id=32 AND file_name LIKE '%高德打车电子行程单.pdf'
        ORDER BY id
    """)
    await conn.close()
    ocr = get_ocr_service()
    for r in rows:
        text, conf, layout = ocr.process_document_pdf(r['file_data'])
        layout_rows = _parse_gaode_layout_rows(text, layout)
        extraction = _extract_itinerary_by_rules(text, conf, layout)
        mapped = _map_public_fields(DocumentType.ITINERARY, extraction)
        ok = _rule_extraction_acceptable(DocumentType.ITINERARY, extraction, mapped, 0.75)
        print('===', r['id'], r['file_name'])
        print('DB :', r['trip_origin'], '|', r['trip_destination'], '|', r['trip_time_text'])
        print('NOW:', mapped.get('trip_origin'), '|', mapped.get('trip_destination'), '|', mapped.get('trip_time_text'), 'ok', ok, 'layout_rows', len(layout_rows), 'layout_items', len(layout))
        if len(layout_rows) == 0:
            print(' raw_text sample:', (r['raw_text'] or '')[:180].replace('\n',' | '))
            print(' new_text sample:', text[:180].replace('\n',' | '))

asyncio.run(main())
