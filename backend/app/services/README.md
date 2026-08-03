# backend/app/services/

核心业务与识别能力。

## 成员

| 文件 | 职责 | 依赖 |
|------|------|------|
| `invoice_service.py` | 发票处理编排：并行 OCR+LLM、字段合并、待审核判定 | ocr/llm、models |
| `batch_service.py` | 批次路径/上传校验、原子登记、特殊规则、后台领取、统计、恢复与重试 | batch model、document_service |
| `document_service.py` | 多资料正文分类、LLM 主提取、整单 OCR/Python 失败兜底、关键字段矩阵与公开字段映射 | ocr/llm、batch model |
| `document_prompts.py` | 通用报销资料分类与字段提取 Prompt 4.0（分类型规则 + 交易级 Schema） | document_service |
| `expense_record_service.py` | 将文件级 JSON 转换为筛选、分类和导出使用的交易级视图 | batch model |
| `document_reconciliation_service.py` | 批次跨文件候选生成、LLM 主辅凭证判断、Python 校验与 JSON 持久化 | expense record、llm、batch model |
| `expense_classification_service.py` | 月度资料费用分类、人民币单币种汇总、发票/支付金额核对与风险说明 | batch model、review reference、llm_service |
| `exclusion_service.py` | 月度资料保留、排除和待复核判定 | company profile、review reference、llm_service |
| `ocr_service.py` | PaddleOCR + 旧发票 PDF 判定 + 通用全页 PDF 文本层 | paddleocr、pdfplumber 等 |
| `llm_service.py` | 多厂商 Vision LLM 调用与 JSON 解析 | prompts、config |
| `prompts.py` | 中国发票 Vision System Prompt / JSON Schema | 被 llm_service 使用 |
| `model_registry.py` | 模型/厂商注册与能力描述 | settings 相关 |
| `audit_service.py` | 写审计日志 | audit_log model |

## 边界

- `invoice_service` 保持原增值税发票双源流程，不依赖新批次模型。
- `document_service` 面向第一阶段多资料类型；业务字段只来自正文，文件名/月份不能补值。
- 月度字段提取 Prompt 在代码中版本化；Prompt 4.0 将一个文件内的订单、行程和转账拆成交易记录并保留金额证据，图片由视觉 LLM 读取原图和 OCR 文字，PDF 使用全文文字。合法 LLM 结果不与规则逐字段核对或补空，仅在调用/响应无效或全部关键字段为空时整单使用 OCR/Python 兜底。
- 文件名仅可作为 `description` 的最后兜底：去掉扩展名后最多保留 500 字符，并写入 `description_source=FILENAME_FALLBACK`；不得用于其他业务字段或模型推断。
- 批次完成后由 Python 生成保守的跨文件候选，核对模型只判断关系和主凭证；Python 校验引用、置信度与同组金额后写入既有 `_reconciliation`。确认重复仅主凭证进入合计，疑似/未知关系保持空额复核。
- 数据库状态和统计属于 `batch_service`；OCR/LLM 层不得直接改批次账本。
