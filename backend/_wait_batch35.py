import time, json, urllib.request
batch_id = 35
deadline = time.time() + 900
while time.time() < deadline:
    with urllib.request.urlopen(f'http://backend:18080/api/batches/{batch_id}', timeout=30) as resp:
        data = json.load(resp)
    status = data.get('status')
    print(
        time.strftime('%H:%M:%S'),
        'status=', status,
        'recognized=', data.get('recognized_files'),
        'manual=', data.get('manual_review_files'),
        'failed=', data.get('failed_files'),
        'skipped=', data.get('skipped_files'),
        'total=', data.get('total_files'),
        flush=True,
    )
    if status in {'COMPLETED', 'FAILED'}:
        print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
        break
    time.sleep(10)
else:
    print('TIMEOUT')
