# backend/app/services/

核心业务与识别能力。

## 成员

| 文件 | 职责 | 依赖 |
|------|------|------|
| `invoice_service.py` | 发票处理编排：并行 OCR+LLM、字段合并、待审核判定 | ocr/llm、models |
| `ocr_service.py` | PaddleOCR + PDF 文本层 + 增值税字段规则抽取 | paddleocr、pdfplumber 等 |
| `llm_service.py` | 多厂商 Vision LLM 调用与 JSON 解析 | prompts、config |
| `prompts.py` | 中国发票 Vision System Prompt / JSON Schema | 被 llm_service 使用 |
| `model_registry.py` | 模型/厂商注册与能力描述 | settings 相关 |
| `audit_service.py` | 写审计日志 | audit_log model |

## 边界

- 产品字段体系面向**增值税发票**；扩展其他票据类型需改 prompts + models + 本层解析。
