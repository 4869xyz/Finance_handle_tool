from openpyxl import load_workbook
from pathlib import Path
import re

def load_rows(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    sheets = {}
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        headers = [str(h).strip() if h is not None else f'col{i}' for i,h in enumerate(rows[0])]
        data = []
        for r in rows[1:]:
            data.append({headers[i]: r[i] if i < len(r) else None for i in range(len(headers))})
        sheets[ws.title] = {'headers': headers, 'rows': data}
    return sheets

def norm(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == 'none':
        return None
    s = s.replace('\n', '').replace('\r', '').replace(' ', '')
    return s

curr = load_rows('/app/_review_excl.xlsx')
manual = load_rows('/app/_apr_manual.xlsx')
print('CURRENT sheets:', {k: len(v['rows']) for k,v in curr.items()})
for name, sh in curr.items():
    print(' sheet', name, 'headers=', sh['headers'])
print('MANUAL sheets:', {k: len(v['rows']) for k,v in manual.items()})

# Prefer 识别结果 or first sheet with 文件名
def pick_result(sheets):
    for key in sheets:
        if '识别' in key or '结果' in key:
            return sheets[key]
    # fallback: sheet with 文件名
    for key, sh in sheets.items():
        if '文件名' in sh['headers']:
            return sh
    return next(iter(sheets.values()))

c = pick_result(curr)
m = pick_result(manual)
print('\nUsing current rows', len(c['rows']), 'manual', len(m['rows']))

# index by filename
def by_name(rows):
    out = {}
    for r in rows:
        name = r.get('文件名')
        if name:
            out[str(name)] = r
    return out

cm = by_name(c['rows'])
mm = by_name(m['rows'])
common = sorted(set(cm) & set(mm))
only_c = sorted(set(cm) - set(mm))
only_m = sorted(set(mm) - set(cm))
print('common', len(common), 'only_current', len(only_c), 'only_manual', len(only_m))
if only_c[:5]:
    print(' only_current sample', only_c[:5])
if only_m[:5]:
    print(' only_manual sample', only_m[:5])

gaode = [n for n in common if '高德打车电子行程单' in n]
print('\n=== GAODE trip field audit (%d files) ===' % len(gaode))

# Acceptance criteria from plan
# 9/9 OD semantic match (normalize newlines/spaces; fix missing paren on 立道)
# 7/7 single trip time range
# 2/2 multi trip segment start times with /
# title pollution = 0

TITLE_BAD = ['高德地图', '行程单', '车型', '上车时间', 'AMAP']

def fix_manual_od(name, field, val):
    v = norm(val)
    if v is None:
        return None
    # known manual typo: missing closing paren on 立道 destination
    if '立道出行' in name and field == '行程终点' and v.endswith('公寓店'):
        v = v + ')'
    return v

ok_od = 0
ok_time = 0
pollution = 0
details = []
for name in gaode:
    cr, mr = cm[name], mm[name]
    c_o, c_d, c_t = norm(cr.get('行程起点')), norm(cr.get('行程终点')), norm(cr.get('行程时间'))
    m_o = fix_manual_od(name, '行程起点', mr.get('行程起点'))
    m_d = fix_manual_od(name, '行程终点', mr.get('行程终点'))
    m_t = norm(mr.get('行程时间'))
    # multi-trip expected time from plan: segment starts, not whole range
    multi = '2个行程' in name or '3个行程' in name
    od_match = (c_o == m_o and c_d == m_d)
    # For multi, manual time is whole-range; our improved target is segment times.
    # Compare OD strictly; for time: single must match manual; multi check slash segments.
    if multi:
        time_ok = bool(c_t and '/' in c_t and all(re.search(r'\d{4}-\d{2}-\d{2}\s*\d{1,2}:\d{2}', p) for p in c_t.split('/')))
        # also origin/dest should have /
        od_match = od_match and ('/' in (c_o or '')) and ('/' in (c_d or ''))
    else:
        time_ok = (c_t == m_t)
    polluted = any(bad in (c_o or '') or bad in (c_d or '') for bad in TITLE_BAD)
    if od_match: ok_od += 1
    if time_ok: ok_time += 1
    if polluted: pollution += 1
    details.append((name, od_match, time_ok, polluted, c_o, m_o, c_d, m_d, c_t, m_t, cr.get('对方'), mr.get('对方'), cr.get('金额'), mr.get('金额')))

print(f'OD semantic match: {ok_od}/{len(gaode)}')
print(f'Time target match: {ok_time}/{len(gaode)}')
print(f'Title pollution: {pollution}')
for d in details:
    name, od_match, time_ok, polluted, c_o, m_o, c_d, m_d, c_t, m_t, c_p, m_p, c_a, m_a = d
    status = 'OK' if od_match and time_ok and not polluted else 'DIFF'
    print(f'\n[{status}] {name}')
    print(f'  provider cur={c_p!r} man={m_p!r} amount cur={c_a} man={m_a}')
    print(f'  origin cur={c_o!r}')
    print(f'         man={m_o!r} match={c_o==m_o}')
    print(f'  dest   cur={c_d!r}')
    print(f'         man={m_d!r} match={c_d==m_d}')
    print(f'  time   cur={c_t!r}')
    print(f'         man={m_t!r} time_ok={time_ok}')

# broader field diffs on common non-skipped
print('\n=== Broader field mismatch rates (common files) ===')
fields = ['资料类型','处理状态','资料日期','对方','摘要','金额','项目名称','发票号码','交易/订单号','订单号','行程起点','行程终点','行程时间']
for f in fields:
    if f not in c['headers'] or f not in m['headers']:
        continue
    mism = 0
    total = 0
    samples = []
    for name in common:
        cv, mv = norm(cm[name].get(f)), norm(mm[name].get(f))
        # skip empty==empty
        total += 1
        if cv != mv:
            mism += 1
            if len(samples) < 3:
                samples.append((name, cv, mv))
    print(f'{f}: mismatch {mism}/{total}')
    for s in samples:
        print(f'   e.g. {s[0][:40]} cur={s[1]!r} man={s[2]!r}')

# current gaode values even if not in manual
print('\n=== All Gaode rows in current export ===')
for name, r in sorted(cm.items()):
    if '高德打车电子行程单' in name:
        print(name)
        print(' ', r.get('处理状态'), r.get('对方'), r.get('金额'), r.get('行程起点'), '|', r.get('行程终点'), '|', r.get('行程时间'))
