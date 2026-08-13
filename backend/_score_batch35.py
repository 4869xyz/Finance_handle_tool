import asyncio, asyncpg, json, re
from decimal import Decimal

# expected from prior golden/manual semantic
EXPECT = {
    '【AA出行-21.90元-1个行程】高德打车电子行程单.pdf': {
        'provider': 'AA出行', 'amount': '21.90',
        'origin': '广州卓远虚拟现实科技股份有限公司',
        'destination': '中创孵化器·元宇宙科幻产业园',
        'time': '2025-04-17 18:55至2025-04-17 19:30',
    },
    '【博度出行-37.75元-1个行程】高德打车电子行程单.pdf': {
        'provider': '博度出行', 'amount': '37.75',
        'origin': '中创孵化器·元宇宙科幻产业园东南侧',
        'destination': '广州卓远虚拟现实科技股份有限公司',
        'time': '2025-04-17 08:11至2025-04-17 08:46',
    },
    '【喜行约车-12.27元-1个行程】高德打车电子行程单.pdf': {
        'provider': '喜行约车', 'amount': '12.27',
        'origin': '中创孵化器·元宇宙科幻产业园东南侧',
        'destination': '板桥地铁站A口',
        'time': '2025-04-12 20:00至2025-04-12 20:08',
    },
    '【喜行约车-12.55元-1个行程】高德打车电子行程单.pdf': {
        'provider': '喜行约车', 'amount': '12.55',
        'origin': '星辰云上STARS西侧',
        'destination': '南村万博(地铁站)',
        'time': '2025-04-05 13:55至2025-04-05 14:02',
    },
    '【携华出行-13.00元-1个行程】高德打车电子行程单.pdf': {
        'provider': '携华出行', 'amount': '13.00',
        'origin': '恒润生活超市(三泰桥兴公寓店)',
        'destination': '中创孵化器·元宇宙科幻产业园',
        'time': '2025-04-06 12:27至2025-04-06 12:36',
    },
    '【曹操出行-29.11元-1个行程】高德打车电子行程单.pdf': {
        'provider': '曹操出行', 'amount': '29.11',
        'origin': '恒润生活超市(三泰桥兴公寓店)',
        'destination': '亚运城·天成2区',
        'time': '2025-04-19 15:06至2025-04-19 15:28',
    },
    '【添猫出行-30.50元-2个行程】高德打车电子行程单.pdf': {
        'provider': '添猫出行', 'amount': '30.50',
        'origin': '地铁板桥站-A口东北侧/地铁板桥站-A口东北侧',
        'destination': '中创孵化器·元宇宙科幻产业园/中创孵化器·元宇宙科幻产业园',
        'time': '2025-04-07 08:29/2025-04-10 08:33',
        'multi': True,
    },
    '【立道出行-11.94元-1个行程】高德打车电子行程单.pdf': {
        'provider': '立道出行', 'amount': '11.94',
        'origin': '中创孵化器·元宇宙科幻产业园东南侧',
        'destination': '恒润生活超市(三泰桥兴公寓店)',
        'time': '2025-04-13 23:05至2025-04-13 23:14',
    },
    '【风韵出行-30.98元-2个行程】高德打车电子行程单.pdf': {
        'provider': '风韵出行', 'amount': '30.98',
        'origin': '地铁板桥站-A口东北侧/广晟万博',
        'destination': '中创孵化器·元宇宙科幻产业园/恒润生活超市(三泰桥兴公寓店)',
        'time': '2025-04-11 08:44/2025-04-13 03:32',
        'multi': True,
    },
}

def norm(v):
    if v is None: return None
    return re.sub(r'\s+', '', str(v))

async def main():
    conn = await asyncpg.connect(host='db', port=5432, user='postgres', password='postgres', database='invoice_db')
    rows = await conn.fetch('''
        SELECT file_name, processing_status, counterparty, amount,
               trip_origin, trip_destination, trip_time_text, extracted_fields_json
        FROM source_documents
        WHERE batch_id=35
        ORDER BY relative_path
    ''')
    await conn.close()
    print('TOTAL DOCS', len(rows))
    ok = fail = 0
    for r in rows:
        if '高德打车电子行程单' not in r['file_name']:
            continue
        raw = r['extracted_fields_json']
        if isinstance(raw, str):
            raw = json.loads(raw)
        v = (raw or {}).get('_validation', {})
        exp = EXPECT.get(r['file_name'], {})
        c_o, c_d, c_t = norm(r['trip_origin']), norm(r['trip_destination']), norm(r['trip_time_text'])
        e_o, e_d, e_t = norm(exp.get('origin')), norm(exp.get('destination')), norm(exp.get('time'))
        amount_ok = (str(r['amount']) if r['amount'] is not None else None) == exp.get('amount')
        # amount may be Decimal 21.90 vs 21.90
        if r['amount'] is not None and exp.get('amount'):
            amount_ok = Decimal(str(r['amount'])) == Decimal(exp['amount'])
        od_ok = c_o == e_o and c_d == e_d
        time_ok = c_t == e_t
        provider_ok = r['counterparty'] == exp.get('provider')
        all_ok = od_ok and time_ok and provider_ok and amount_ok and r['processing_status']=='RECOGNIZED'
        if all_ok: ok += 1
        else: fail += 1
        print(('OK' if all_ok else 'DIFF'), r['file_name'])
        print('  status', r['processing_status'], 'source', v.get('final_source'), 'path', v.get('escalation_path'))
        print('  provider', r['counterparty'], 'amount', r['amount'])
        print('  origin', r['trip_origin'])
        print('  dest  ', r['trip_destination'])
        print('  time  ', r['trip_time_text'])
        if not all_ok:
            print('  EXPECT origin', exp.get('origin'))
            print('  EXPECT dest  ', exp.get('destination'))
            print('  EXPECT time  ', exp.get('time'))
    print(f'\nGAODE SCORE {ok}/{ok+fail}')

    print('\n=== Non-gaode needing attention ===')
    for r in rows:
        if '高德打车电子行程单' in r['file_name']:
            continue
        if r['processing_status'] in ('NEEDS_MANUAL_REVIEW', 'FAILED'):
            print(r['processing_status'], r['file_name'], r['counterparty'], r['amount'])

asyncio.run(main())
