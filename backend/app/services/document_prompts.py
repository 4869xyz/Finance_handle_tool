DOCUMENT_JSON_SYSTEM_PROMPT = """你是财务报销资料识别助手。
只依据输入资料正文判断和提取，不得把文件名或目录名当成业务字段。
只返回一个严格 JSON 对象（JSON），不要返回 Markdown 代码块、解释或额外字段。
看不清或资料中不存在的值必须为 null（不要用空字符串 ""），禁止推测。
confidence 必须是 0 到 1 之间的数字，不能是字符串。"""


DOCUMENT_CLASSIFICATION_PROMPT = """请判断以下报销资料正文的类型。
document_type 只能是以下英文枚举值之一（禁止中文）：
VAT_INVOICE, ELECTRONIC_INVOICE, TAXI_INVOICE, ITINERARY,
TRAIN_OR_AIR_TICKET, PAYMENT_SCREENSHOT, ORDER_SCREENSHOT,
OVERSEAS_INVOICE_OR_RECEIPT, RECEIPT, OTHER_DOCUMENT, UNKNOWN。

优先级：电子/数电发票归 ELECTRONIC_INVOICE；纸质增值税发票归 VAT_INVOICE；
纸质出租车票归 TAXI_INVOICE；电子行程资料归 ITINERARY。
支付凭证与订单按正文主要语义区分，不按文件格式区分。

严格返回 JSON：
{{"document_type":"枚举英文值","confidence":0.0到1.0的数字}}

正文：
{text}
"""


DOCUMENT_EXTRACTION_PROMPT = """请从以下 {document_type} 报销资料正文提取字段。
严格返回下列全部字段的 JSON，不能增加字段；未知值为 null（禁止空字符串）：
{{
  "document_date": "YYYY-MM-DD或null",
  "counterparty": "对方名称或null",
  "description": "交易或资料摘要或null",
  "amount": "资料的可信汇总金额数字或null",
  "currency": "资料正文明确出现的币种代码或null",
  "invoice_number": "发票号码或null",
  "transaction_number": "支付交易号或订单号或null",
  "provider": "行程服务商或null",
  "seller": "发票销售方或null",
  "payee": "收款方或null",
  "merchant": "商户或null",
  "total_with_tax": "发票价税合计或null",
  "payment_amount": "支付金额或null",
  "trip_amount": "行程汇总金额或null",
  "order_number": "订单号或null",
  "aggregate_amount": "多行程或多项目的明确汇总金额或null",
  "transactions": [
    {{
      "document_date": "YYYY-MM-DD或null",
      "counterparty": "对方名称或null",
      "amount": "金额数字或null",
      "transaction_number": "交易号或null"
    }}
  ],
  "confidence": 0.0到1.0的数字
}}

amount 只能填写正文明确表达的总额。存在多笔独立交易且没有明确汇总值时，
amount、payment_amount 和 aggregate_amount 必须为 null，并在 transactions 中逐笔保留。

正文：
{text}
"""
