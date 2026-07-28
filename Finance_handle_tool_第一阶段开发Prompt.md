# Finance_handle_tool 第一阶段开发 Prompt

请在现有项目中完成第一阶段改造。项目路径：

`F:\Dene_internship\0721finance_charge_reimbursement\Finance_handle_tool`

你是本项目的开发代理。请直接检查现有代码、制定小步实施计划、完成代码修改、运行测试并修复问题。不要只给建议，也不要在未阅读项目代码的情况下重写架构。

## 1. 开始前必须执行

1. 完整阅读项目根目录的 `AGENTS.md`、`ARCHITECTURE.md`、`README.md`。
2. 阅读与本任务直接相关的现有代码，至少包括：
   - `backend/app/models/invoice.py`
   - `backend/app/schemas/invoice.py`
   - `backend/app/routers/invoices.py`
   - `backend/app/services/ocr_service.py`
   - `backend/app/services/llm_service.py`
   - `backend/app/services/invoice_service.py`
   - `backend/app/services/prompts.py`
   - `backend/app/config.py`
   - `backend/app/main.py`
   - `frontend/src/pages/UploadPage.tsx`
   - `frontend/src/services/api.ts`
   - `frontend/src/types/invoice.ts`
3. 先说明你对现有上传、OCR、视觉模型、数据库和前端流程的理解。
4. 在编码前列出明确的成功标准、预计修改文件和验证命令。
5. 保留用户现有未提交修改，不要重置、覆盖或顺手重构无关代码。

## 2. 业务背景

本系统每次只处理“一个员工的一个月份”，不会一次输入全年或多人的大量资料。

一个月的输入目录固定包含三个业务文件夹：

```text
员工姓名-YYYY-MM/
├── 发票/
├── 补票/
└── 支付凭证/
```

业务含义：

- `发票`：本月正常取得的发票。
- `补票`：替代缺票金额的发票。它本质上仍是发票，只是来源角色不同。
- `支付凭证`：微信、支付宝、银行卡、订单页、行程单等付款或消费证明。

以后会执行以下流程，但本次只实现第一阶段：

1. 支付凭证形成一个分类表。
2. 发票与补票合并形成一个分类表。
3. 两个表使用同一套费用分类。
4. 最终形成汇总表。

## 3. 本次唯一目标

将现有“只能识别标准中国发票”的项目，改造成支持“单人、单月、三个来源文件夹”的多类型票据识别系统。

本次只实现：

- 月度批次输入。
- 三个来源文件夹识别。
- 文件登记和处理状态。
- 多种票据/凭证的材料类型识别。
- 按材料类型提取核心字段。
- 无法可靠识别时标记为需要人工处理。
- 展示月度批次及识别结果。

本次明确不实现：

- 费用分类。
- 发票与支付凭证自动匹配。
- 公司业务相关性判断。
- 自动剔除。
- 发票真伪联网查验。
- 支付凭证分类表。
- 发票与补票分类表。
- 最终汇总表。
- 报销政策和金额限额。
- 项目、客户、出差或审批数据匹配。
- 合并PDF拆页、拆票或逐页形成多条发票记录。

不要提前开发上述功能，不要因为“以后可能需要”而增加与本阶段无关的复杂抽象。

## 4. 两条不可违反的特殊规则

### 4.1 视频文件

视频文件不执行OCR、不调用视觉模型、不抽帧、不解析。

如果输入目录中出现 `.mp4`、`.mov`、`.avi`、`.mkv` 等视频：

- 允许其进入月度批次文件清单。
- 保存原文件名、来源文件夹、格式、大小等元数据。
- 状态直接设为 `NEEDS_MANUAL_REVIEW`。
- 原因设为 `UNSUPPORTED_VIDEO`。
- 用户界面显示“视频文件暂不处理，需要人工处理”。
- 必须有测试证明视频不会调用OCR或LLM服务。

### 4.2 汇总PDF

当前名为 `PDF合并.pdf` 的文件只是已存在发票的重复汇总件，本阶段不处理。

处理规则：

- 对文件名进行安全的大小写和首尾空格标准化。
- 当文件基本名精确匹配 `PDF合并.pdf` 时，不执行OCR、不调用视觉模型、不进行PDF拆页。
- 文件仍登记在批次中。
- 状态设为 `SKIPPED`。
- 原因设为 `DUPLICATE_SUMMARY_PDF`。
- 用户界面显示“重复汇总PDF，已跳过”。
- 不要使用过宽规则，例如不能因为普通文件名里包含“合并”两个字就全部跳过。
- 必须有测试证明该文件不会调用OCR或LLM，同时其他普通PDF仍会正常处理。

## 5. 关键领域概念

必须在数据和代码中区分以下概念。

### 5.1 来源文件夹 `source_folder`

表示文件来自哪个业务目录：

```text
INVOICE
REPLACEMENT_INVOICE
PAYMENT_EVIDENCE
```

### 5.2 材料类型 `document_type`

表示文件内容是什么。第一阶段至少支持以下枚举：

```text
VAT_INVOICE
ELECTRONIC_INVOICE
TAXI_INVOICE
ITINERARY
TRAIN_OR_AIR_TICKET
PAYMENT_SCREENSHOT
ORDER_SCREENSHOT
OVERSEAS_INVOICE_OR_RECEIPT
RECEIPT
OTHER_DOCUMENT
UNKNOWN
VIDEO
```

如果现有样本和代码表明需要合并某些枚举，可以合理简化，但必须保留“来源文件夹”和“材料类型”两个不同维度。

特别注意：

- `补票`不是 `document_type`。
- 补票中的电子发票应当是：

```text
source_folder = REPLACEMENT_INVOICE
document_type = ELECTRONIC_INVOICE
```

### 5.3 处理状态 `processing_status`

建议至少包含：

```text
UPLOADED
PROCESSING
RECOGNIZED
NEEDS_MANUAL_REVIEW
SKIPPED
FAILED
```

状态原因必须单独保存，不能只依赖一段不可查询的错误文本。

## 6. 推荐的最小数据设计

不要破坏现有 `Invoice`、`OcrResult`、`LlmResult` 和现有发票接口。现有标准发票流程应继续工作。

建议新增独立模型，不要把支付截图硬塞进现有 `Invoice` 表。

### 6.1 `ProcessingBatch`

代表一个员工一个月的一次输入，至少包含：

```text
id
employee_name
expense_month
status
total_files
recognized_files
manual_review_files
skipped_files
failed_files
created_at
updated_at
```

`expense_month`必须使用明确的年月格式，不能用自由文本。

### 6.2 `SourceDocument`

代表一个实际输入文件，至少包含：

```text
id
batch_id
source_folder
relative_path
file_name
file_type
file_size
file_hash
file_data
document_type
processing_status
status_reason
raw_text
classification_confidence
extraction_confidence
document_date
counterparty
description
amount
currency
invoice_number
transaction_number
extracted_fields_json
created_at
updated_at
```

要求：

- 公共日期、交易方、描述、金额和币种使用普通字段，方便后续两个分类表共用。
- 各材料类型的专属字段放入 `extracted_fields_json`。
- 金额使用精确数值类型，不要使用浮点数。
- 保存原始OCR文本和模型解析结果的来源，以便人工检查。
- 使用文件SHA-256作为文件指纹；本次不需要开发复杂的相似图片去重。
- 如果项目目前依赖 `Base.metadata.create_all`，优先通过新增表完成本阶段，避免假设 `create_all` 会自动修改已有表结构。

具体命名应遵循现有项目代码风格，不要为了完全照抄上面的名称而破坏项目一致性。

## 7. 月度批次输入

在不破坏现有 `/api/invoices/upload` 的前提下，新增批次输入能力。

推荐新增独立API，例如：

```text
POST /api/batches
GET  /api/batches
GET  /api/batches/{batch_id}
GET  /api/batches/{batch_id}/documents
POST /api/batches/{batch_id}/reprocess
```

上传参数至少包含：

```text
employee_name
expense_month
files[]
relative_paths[]
```

要求：

- `files`与`relative_paths`严格按索引对应。
- 从相对路径识别 `发票`、`补票`、`支付凭证`。
- 目录结构缺失、路径越界、未知顶层目录时返回明确错误。
- 做路径标准化和安全校验，不能信任浏览器提交的相对路径。
- 不允许相对路径中的 `..`、绝对路径或盘符穿越。
- 批次总量限制与单文件限制分开配置。
- 新批次接口允许视频格式进入清单，但视频绝不进入识别流程。
- 现有普通发票上传接口行为保持不变。

前端可以使用目录选择上传，读取文件对象的相对路径；如果现有Ant Design版本支持目录上传，优先沿用现有组件能力，不引入新的前端框架。

## 8. 识别流水线

实现一个清晰、可测试的处理流水线：

```text
文件登记
  -> 特殊规则判断
  -> 文本/OCR提取
  -> 材料类型分类
  -> 对应字段提取
  -> 字段标准化与校验
  -> 保存结果和置信度
  -> 更新批次统计
```

执行顺序必须是：

1. 视频判断。
2. `PDF合并.pdf`判断。
3. 其他普通文件识别。

视频和汇总PDF在步骤1或2结束，不能进入后面的OCR和LLM逻辑。

普通文件处理建议：

- PDF有可用文本层时优先使用文本层。
- 图片和扫描件复用现有PaddleOCR。
- 材料类型简单明确时使用确定性关键词和结构判断。
- 规则无法可靠判断时再调用现有视觉模型。
- 不要为了所有文件都运行两次模型而增加不必要成本。
- 任何解析异常都要转换成可查询的处理状态，不能使整个批次失败。

由于本阶段明确跳过 `PDF合并.pdf`，不要开发通用PDF拆票功能。普通多页PDF可以作为一个来源文档处理，OCR文本可以读取全部页面，但视觉解析是否只取首页应保持与现有代码兼容并明确记录这个限制。

## 9. 材料分类和字段提取

不要继续用一套“中国标准发票12字段”提示词处理所有材料。

建议拆分为：

```text
document_classification_prompt
invoice_extraction_prompt
payment_evidence_extraction_prompt
itinerary_extraction_prompt
overseas_receipt_extraction_prompt
generic_document_extraction_prompt
```

所有模型输出必须：

- 使用严格JSON对象。
- 通过Pydantic Schema校验。
- 无法识别的字段返回 `null`。
- 不允许猜测不存在的税号、金额、日期和交易号。
- JSON解析失败时进入安全降级流程。
- 保留原始模型响应用于诊断，但不要在日志中输出API密钥或敏感配置。

### 9.1 发票类核心字段

```text
invoice_number
invoice_code
issue_date
buyer_name
buyer_tax_id
seller_name
seller_tax_id
item_name
amount
tax_amount
total_with_tax
tax_rate
currency
```

### 9.2 支付凭证类核心字段

```text
payment_date
payment_amount
currency
payer
payee
payment_channel
transaction_number
order_number
description
```

### 9.3 行程单类核心字段

```text
trip_date
provider
passenger
origin
destination
trip_amount
currency
order_number
trip_count
```

### 9.4 境外票据类核心字段

```text
document_date
merchant
description
amount
currency
invoice_or_order_number
payment_method
```

将不同类型的日期、交易方、描述、金额、币种映射到 `SourceDocument` 的公共字段，同时将完整类型专属结果保存到JSON字段中。

## 10. 前端最小改造

不要重做整个界面。

在保持现有发票上传和列表功能可用的前提下，增加一个“月度资料识别”入口，至少提供：

1. 员工姓名输入。
2. 报销月份选择。
3. 月度文件夹选择。
4. 上传前显示三个来源文件夹的文件数量。
5. 上传后显示批次处理进度和统计：
   - 总文件数
   - 已识别
   - 需人工处理
   - 已跳过
   - 失败
6. 批次详情表至少显示：
   - 来源文件夹
   - 文件名
   - 材料类型
   - 日期
   - 交易方
   - 描述
   - 金额与币种
   - 发票号或交易号
   - 置信度
   - 处理状态
   - 状态原因

视频显示“视频文件暂不处理，需要人工处理”。

`PDF合并.pdf`显示“重复汇总PDF，已跳过”。

不要在本阶段增加费用类别、业务相关性、匹配关系和汇总图表。

## 11. 兼容性要求

- 现有标准发票上传、OCR、视觉模型比对、人工确认、列表和Excel导出功能不能退化。
- 不删除或重命名现有API。
- 不修改现有发票字段语义。
- 复用现有OCR和LLM Provider，不另外引入第二套模型配置系统。
- 不更换React、FastAPI、SQLAlchemy、PostgreSQL、PaddleOCR等现有技术栈。
- 不引入消息队列、对象存储、微服务或新的前端状态框架。
- 不做与本任务无关的格式化和重构。

## 12. 必须补充的测试

后端至少增加以下测试：

1. `发票`路径正确映射为 `INVOICE`。
2. `补票`路径正确映射为 `REPLACEMENT_INVOICE`。
3. `支付凭证`路径正确映射为 `PAYMENT_EVIDENCE`。
4. 含 `..`、绝对路径或未知顶层目录的相对路径被拒绝。
5. `.mp4`创建文档记录并标记 `NEEDS_MANUAL_REVIEW / UNSUPPORTED_VIDEO`。
6. 视频处理过程中OCR和LLM Mock均未被调用。
7. 精确文件名 `PDF合并.pdf`标记 `SKIPPED / DUPLICATE_SUMMARY_PDF`。
8. 汇总PDF处理过程中OCR和LLM Mock均未被调用。
9. 普通PDF不会被错误跳过。
10. 标准电子发票仍可提取现有字段。
11. 支付截图JSON能够映射到公共金额、日期、交易方和描述字段。
12. 无效模型JSON不会导致整个批次失败，而是进入人工处理或失败状态。
13. 批次统计与其文档状态数量一致。
14. 现有测试全部继续通过。

如果项目已有测试模式和fixture，请沿用，不要新建不一致的测试体系。

## 13. 验收标准

只有同时满足以下条件才能宣布本任务完成：

- 用户能够输入一个员工、一个月份和包含三个固定子目录的月度文件夹。
- 每个输入文件都生成一条可查询记录，不静默丢失。
- 视频不执行任何自动识别，统一标记人工处理。
- `PDF合并.pdf`不执行任何自动识别，统一标记跳过。
- 发票与补票使用同一套发票识别逻辑，但保留不同来源角色。
- 支付凭证不会因为缺少税号等发票字段而被强制判定失败。
- 支持的材料类型能够输出统一公共字段和类型专属JSON。
- 无法可靠识别的材料进入人工处理。
- 前端能够查看批次进度和每个文件的结果。
- 现有标准发票功能仍正常。
- 后端测试通过。
- 前端构建通过。
- 没有未说明的跳过项或未验证项。

## 14. 建议验证命令

根据项目实际环境调整，但至少执行：

```text
cd backend
pytest

cd ../frontend
npm run build
```

如果项目依赖Docker/PostgreSQL才能进行集成验证，应运行对应的现有项目命令；如果环境无法启动，必须明确说明哪些部分只完成了单元测试、哪些未能集成验证。

## 15. 实施方式

请按以下顺序推进并在每一步后简短汇报：

1. 代码与架构检查。
2. 数据模型和Schema。
3. 路径解析及特殊文件规则。
4. 批次上传API。
5. 多材料分类与字段提取。
6. 前端月度批次上传和结果页。
7. 单元测试与回归测试。
8. 前端构建和最终验收。

遇到不确定点时，优先从现有代码和样本中查证。只有当不同选择会明显改变数据模型或用户流程时才向用户提问。不要把纯实现细节反问给用户。

最终回复必须包含：

- 实际修改的文件。
- 新增的数据模型和API。
- 视频和汇总PDF规则的实现位置。
- 测试及构建结果。
- 尚未解决的限制。
- 下一阶段建议，但不要在本次代码中实现下一阶段功能。
