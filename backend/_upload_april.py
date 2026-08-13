from pathlib import Path
import httpx

root = Path('/app/_april_case')
files = sorted([p for p in root.rglob('*') if p.is_file()], key=lambda p: str(p))
# skip nothing under source folders; all 32 are valid extensions
multipart = []
rel_paths = []
file_handles = []
try:
    data = {
        'employee_name': '马耿宾',
        'expense_month': '2025-04',
    }
    # httpx multipart: list of tuples
    parts = [
        ('employee_name', (None, '马耿宾')),
        ('expense_month', (None, '2025-04')),
    ]
    for p in files:
        rel = str(p.relative_to(root)).replace('\\', '/')
        fh = p.open('rb')
        file_handles.append(fh)
        parts.append(('relative_paths', (None, rel)))
        parts.append(('files', (p.name, fh, 'application/octet-stream')))
    print(f'uploading {len(files)} files...')
    with httpx.Client(base_url='http://backend:18080', timeout=180.0) as client:
        resp = client.post('/api/batches', files=parts)
    print('status', resp.status_code)
    print(resp.text[:2000])
finally:
    for fh in file_handles:
        fh.close()
