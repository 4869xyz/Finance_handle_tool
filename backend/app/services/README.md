# backend/app/services/

核心业务与识别能力。

## 成员

| 文件 | 职责 | 依赖 |
|------|------|------|
| `invoice_service.py` | 发票处理编排：并行 OCR+LLM、字段合并、待审核判定 | ocr/llm、models |
| `batch_service.py` | 批次路径/上传校验、原子登记、特殊规则、后台领取、统计、恢复与重试 | batch model、document_service |
| `document_service.py` | 多资料正文分类、严格 JSON 提取、关键字段矩阵、公开字段映射 | ocr/llm、batch model |
| `document_prompts.py` | 通用报销资料分类与字段提取 Prompt | document_service |
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
- 数据库状态和统计属于 `batch_service`；OCR/LLM 层不得直接改批次账本。
