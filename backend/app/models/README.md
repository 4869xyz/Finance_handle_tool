# backend/app/models/

SQLAlchemy 表模型（数据库结构定义）。

## 成员

| 文件 | 职责 | 依赖 |
|------|------|------|
| `invoice.py` | `invoices` / `ocr_results` / `llm_results` / `parsing_diffs` | `database.Base` |
| `batch.py` | `processing_batches` / `source_documents`、来源/类型/处理状态枚举 | `database.Base` |
| `audit_log.py` | `audit_logs` 审计日志表 | `database.Base` |

## 说明

- 发票原文件存在 `invoices.file_data`（LargeBinary），不落本地 uploads。
- 月度资料通常存在 `source_documents.file_data`；视频只保存元数据和 SHA-256，二进制为空。
- `file_hash` 用于审计，不设唯一约束，也不触发图片相似去重。
- 改字段时需同步：`schemas/`、前端 `types/`、OCR/LLM 抽取逻辑。
