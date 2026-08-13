DOCUMENT_JSON_SYSTEM_PROMPT = """你是财务报销资料识别助手。
只依据输入资料正文判断和提取，不得把文件名或目录名当成业务字段。
只返回一个严格 JSON 对象（JSON），不要返回 Markdown 代码块、解释或额外字段。
看不清或资料中不存在的值必须为 null（不要用空字符串 ""），禁止推测。
confidence 必须是 0 到 1 之间的数字，不能是字符串。"""


DOCUMENT_EXTRACTION_PROMPT_VERSION = "4.3"


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


DOCUMENT_EXTRACTION_COMMON_RULES = """只依据本次提供的 OCR 正文或图片中实际可见的内容：
1. 不得使用文件名、目录名、批次月份、其他文件或常识补全字段，也不得模糊猜测。
2. 可以在内部核对候选值，但最终只能返回 JSON，不得输出推理过程、说明或 Markdown。
3. 日期统一为 YYYY-MM-DD；金额只返回数字，不要包含货币符号、千分位或单位。
4. 优先提取正文明确给出的总额。只有没有明确总额、所有组成金额和加减关系都清晰时才允许计算；必须逐项返回 amount_components，并把 amount_derivation 标记为 CALCULATED。
5. 优惠、税额、单价、余额不能冒充实付或合计；组成不完整或关系不清时不得计算。
6. description 只概括正文明确出现的商品、服务、用途或备注，不得推断费用分类。
7. 金额、号码、日期或对方存在冲突且无法可靠区分时返回 null，并降低 confidence。
8. 多笔独立交易没有明确总额且不满足受控计算条件时，amount、payment_amount、trip_amount、total_with_tax、aggregate_amount 均返回 null，在 transactions 中逐笔保留。
9. 先按订单卡片、行程行、转账卡片或独立凭证区域拆分 records；同一独立记录只能出现一次。
10. 所有金额必须使用两位小数字符串，并在对应 amount 对象中保存实际可见的原文 evidence；没有证据时 value 必须为 null。
11. 原价、划线价、优惠、红包、满减、余额和商品数量不能作为主金额；不得把“共10种”等数量识别为金额。
12. 本阶段只判断一个文件内部的记录，不得猜测它与其他文件是否重复；跨文件主辅凭证由后续批次核对处理。
"""


INVOICE_EXTRACTION_RULES = """发票取值规则：
- document_date 取开票日期，不取打印日期、上传日期或行程日期。
- seller 和 counterparty 取销售方/销方名称，不取购买方、申请人、税务机关或平台抬头。
- total_with_tax 取价税合计（小写）对应金额；amount 仅在正文另有明确总额时填写。
- invoice_number 只取发票号码，不得填发票代码、校验码、税号、订单号或机器编号。
- item_name 只取货物或应税劳务名称/项目名称（如“运输服务*客运服务费”）；禁止把数量、单价、税率、税额、价税合计数字拼进 item_name。
- description 只写简短业务摘要，不得用明细行堆砌替代 item_name；多行项目写入 records[].items，文件级 item_name 用可见项目名以“; ”拼接。
- 出租车纸票没有“销售方”标签时，可将正文明确的出租汽车公司填入 seller；无法确认则为 null。
"""


PAYMENT_EXTRACTION_RULES = """支付凭证取值规则：
- document_date 取支付成功、付款完成或转账完成时间，不取订单创建时间或账单生成时间。
- payee 和 counterparty 取收款方，不取付款人、申请人、银行卡户名或支付平台名称。
- payment_amount 取本次实际支付/转账金额，不取优惠、原价、余额、手续费或累计金额。
- transaction_number 只取交易单号、支付单号或转账流水号；不得把订单号/商家订单号填入 transaction_number。
- order_number 只取订单号、商家订单号或商户订单号；正文没有则 order_number 为 null，禁止用交易单号冒充。
- description 概括支付事项或商品用途；与对方、金额、号码分开填写。
"""


ORDER_EXTRACTION_RULES = """订单取值规则：
- document_date 取订单创建、下单或支付日期中正文明确且最能代表该订单的日期；有支付完成日期时优先支付完成日期。
- merchant 和 counterparty 取店铺、商户或服务提供方，不取买家、收货人或平台名称。
- aggregate_amount 取明确标注的实付、订单合计或应付总额，不取商品单价、优惠额、运费或划线价。
- order_number 只取订单号/商家订单号；支付交易号应填 transaction_number，二者不得互换。
- item_name 汇总本文件可见商品或服务名称；多商品写入 records[].items，文件级 item_name 用“; ”拼接。
- 一张图片包含多个订单卡片时，按商家、订单时间和页面区域分别生成 records；每个订单只提取一次主金额。
- 主金额优先取实付款、支付金额、实际支付、订单总额、合计或应付金额；其次才是全部可见商品到手价的完整合计。
- 商品到手价与订单实付款不一致时，不得修改实付款；分别保留并标记 manual_review。
"""


ITINERARY_EXTRACTION_RULES = """行程及车票取值规则：
- document_date 取实际出行、乘车、乘机或服务发生日期，不取预订、支付、打印或申请日期。
- provider 和 counterparty 取承运方、出租汽车公司或行程服务商（如曹操出行、喜行约车、高德打车），不取乘客、付款人或开票平台。
- 禁止把表格表头词（车型、上车时间、城市、起点、终点、金额）填入 provider、counterparty 或 trip 字段。
- trip_amount 取票价、车费或正文明确的行程汇总金额，不取单段金额之外自行计算的总和。
- 有多段行程且没有明确合计时，汇总金额返回 null，并在 transactions/records 中逐段保留。
- 高德/打车电子行程单：从表体行提取上车时间、起点、终点与金额；页首合计放入 trip.stated_total，逐行金额放入 trip.detail_amounts。
- 每条 TRIP 记录只要正文可见就必须填写 trip.origin、trip.destination，以及 request_time 或 start_time/end_time；看不清则为 null，禁止编造。
- 高德表体每一行必须对应一条 TRIP，严格保持表格顺序和重复地点；不得去重、合并或交换不同记录的起点与终点。
- “高德地图—打车——行程单”、AMAP ITINERARY 等标题及其碎片绝不是起点、终点或服务商。
- 多行程页首的整体“行程时间”范围不等于每一段的起止时间；表体只有上车时间时，仅填写各段 start_time，不得编造 end_time。
"""

GENERIC_EXTRACTION_RULES = """收据及其他资料取值规则：
- 只提取正文明确出现的交易日期、收款方/商户、金额、号码和事项。
- merchant、payee、counterparty 按正文标签填写；无法区分交易双方角色时返回 null。
- aggregate_amount 或 amount 只取明确标注的合计、实付、应付或收款金额。
- 如有明确项目或商品名称可填 item_name；微信聊天或转账截图中的每张转账卡片生成一条 TRANSFER 记录；结合卡片左右位置和状态文字判断 TRANSFER_OUT、TRANSFER_IN 或 DIRECTION_UNKNOWN，不能把转入资金当成报销支出。
"""


DOCUMENT_TYPE_EXTRACTION_RULES = {
    "VAT_INVOICE": INVOICE_EXTRACTION_RULES,
    "ELECTRONIC_INVOICE": INVOICE_EXTRACTION_RULES,
    "TAXI_INVOICE": INVOICE_EXTRACTION_RULES,
    "PAYMENT_SCREENSHOT": PAYMENT_EXTRACTION_RULES,
    "ORDER_SCREENSHOT": ORDER_EXTRACTION_RULES,
    "ITINERARY": ITINERARY_EXTRACTION_RULES,
    "TRAIN_OR_AIR_TICKET": ITINERARY_EXTRACTION_RULES,
    "OVERSEAS_INVOICE_OR_RECEIPT": GENERIC_EXTRACTION_RULES,
    "RECEIPT": GENERIC_EXTRACTION_RULES,
    "OTHER_DOCUMENT": GENERIC_EXTRACTION_RULES,
    "UNKNOWN": GENERIC_EXTRACTION_RULES,
}


DOCUMENT_EXTRACTION_PROMPT = """字段提取 Prompt 版本：{prompt_version}
请从以下 {document_type} 报销资料正文提取字段。

公共约束：
{common_rules}
当前单据类型专用规则：
{type_rules}

严格返回下列全部字段的 JSON，不能增加字段；未知值为 null（禁止空字符串）：
{{
  "document_date": "YYYY-MM-DD或null",
  "counterparty": "对方名称或null",
  "description": "交易或资料摘要或null",
  "amount": "资料的可信汇总金额数字或null",
  "item_name": "项目名称或商品名称汇总或null",
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
  "amount_derivation": "EXPLICIT|CALCULATED|NOT_FOUND",
  "amount_evidence": "原文金额标签、原文片段或计算表达式；无依据时为null",
  "amount_components": [
    {{
      "label": "组成金额的原文标签或null",
      "amount": "金额数字",
      "operation": "ADD|SUBTRACT"
    }}
  ],
  "transactions": [
    {{
      "document_date": "YYYY-MM-DD或null",
      "counterparty": "对方名称或null",
      "amount": "金额数字或null",
      "transaction_number": "交易号或null"
    }}
  ],
  "records": [
    {{
      "record_id": "R1",
      "record_type": "INVOICE|ORDER|TRIP|PAYMENT|TRANSFER|RECEIPT|OTHER",
      "document_date": "YYYY-MM-DD或null",
      "counterparty": "对方、商户或服务商名称或null",
      "description": "本笔记录摘要或null",
      "invoice_number": "发票号码或null",
      "order_number": "订单号或null",
      "transaction_number": "交易号或null",
      "primary_amount": {{"value":"两位小数字符串或null","evidence":"原文金额文字或null","kind":"TOTAL_WITH_TAX|PAID|ORDER_TOTAL|TRIP_TOTAL|TICKET_PRICE|TRANSFER|RECEIPT_TOTAL|ITEM_SUM|UNKNOWN"}},
      "item_subtotal": {{"value":"两位小数字符串或null","evidence":"原文或计算依据或null","kind":"ITEM_SUM|UNKNOWN"}},
      "original_amount": {{"value":"两位小数字符串或null","evidence":"原价文字或null","kind":"ORIGINAL|UNKNOWN"}},
      "discount_amount": {{"value":"两位小数字符串或null","evidence":"优惠文字或null","kind":"DISCOUNT|UNKNOWN"}},
      "items": [{{"name_visible":"商品名可见部分或null","name_complete":false,"specification":"规格或null","quantity":"数量原文或null","item_amount":{{"value":"两位小数字符串或null","evidence":"商品金额原文或null","kind":"ITEM|UNKNOWN"}},"original_amount":{{"value":"两位小数字符串或null","evidence":"商品原价原文或null","kind":"ORIGINAL|UNKNOWN"}},"merchant":"商家或null"}}],
      "trip": {{"request_time":"申请时间原文或null","start_time":"行程开始时间原文或null","end_time":"行程结束时间原文或null","service_provider":"服务商或null","vehicle_type":"车型或null","city":"城市或null","origin":"起点或null","destination":"终点或null","stated_total":{{"value":"两位小数字符串或null","evidence":"页首合计原文或null","kind":"TRIP_TOTAL|UNKNOWN"}},"detail_amounts":[{{"value":"两位小数字符串或null","evidence":"逐行金额原文或null","kind":"TRIP_DETAIL|UNKNOWN"}}]}},
      "transfer": {{"direction":"TRANSFER_OUT|TRANSFER_IN|DIRECTION_UNKNOWN","card_side":"LEFT|RIGHT|UNKNOWN","status_text":"状态原文或null"}},
      "manual_review": false,
      "review_reasons": [],
      "confidence": 0.0
    }}
  ],
  "confidence": 0.0到1.0的数字
}}

金额来自正文明确总额时 amount_derivation 为 EXPLICIT，amount_evidence 返回对应标签或原文片段，amount_components 为空数组。
金额由清晰组成项计算时 amount_derivation 为 CALCULATED，amount_evidence 返回计算表达式，amount_components 必须完整列出加减项。
未找到可靠金额时 amount_derivation 为 NOT_FOUND，所有汇总金额为 null，amount_evidence 为 null，amount_components 为空数组。
records 必须覆盖文件中每笔独立交易；没有可识别交易时返回空数组。商品名出现省略号或被截断时 name_complete 必须为 false，只保留可见文字。

正文：
{text}
"""


def build_document_extraction_prompt(document_type: str, text: str) -> str:
    type_rules = DOCUMENT_TYPE_EXTRACTION_RULES.get(
        document_type,
        GENERIC_EXTRACTION_RULES,
    )
    return DOCUMENT_EXTRACTION_PROMPT.format(
        prompt_version=DOCUMENT_EXTRACTION_PROMPT_VERSION,
        document_type=document_type,
        common_rules=DOCUMENT_EXTRACTION_COMMON_RULES,
        type_rules=type_rules,
        text=text,
    )
