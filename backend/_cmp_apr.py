from openpyxl import load_workbook

def load_rows(path):
    wb = load_workbook(path, data_only=True)
    ws = wb["识别结果"]
    headers = [c.value for c in ws[1]]
    rows = []
    for r in range(2, ws.max_row + 1):
        rows.append({headers[i]: ws.cell(r, i + 1).value for i in range(len(headers))})
    return rows

auto = load_rows("/app/_apr_auto.xlsx")
manual = load_rows("/app/_apr_manual.xlsx")

print("=== TRIP ROWS DETAIL ===")
for a, m in zip(auto, manual):
    if "行程单" not in str(a.get("文件名") or ""):
        continue
    print(f"\nFILE: {a.get('文件名')}")
    for f in ["对方","摘要","金额","资料日期","行程起点","行程终点","行程时间","处理状态"]:
        print(f"  {f}: A={a.get(f)!r} | M={m.get(f)!r}")

print("\n=== INVOICE SAMPLE ===")
for a, m in zip(auto, manual):
    if a.get("来源") != "发票":
        continue
    print(f"FILE={a.get('文件名')} item={a.get('项目名称')!r} inv={a.get('发票号码')!r} amt={a.get('金额')!r} party={a.get('对方')!r}")

print("\n=== PAYMENT NON-TRIP SAMPLE ===")
for a, m in zip(auto, manual):
    if a.get("来源") != "支付凭证" or "行程单" in str(a.get("文件名") or ""):
        continue
    print(f"FILE={a.get('文件名')} type={a.get('资料类型')} party={a.get('对方')!r} desc={a.get('摘要')!r} amt={a.get('金额')!r} tx={a.get('交易/订单号')!r} ord={a.get('订单号')!r}")
