# backend/app/models/

SQLAlchemy 表模型（数据库结构定义）。

## 成员

| 文件 | 职责 | 依赖 |
|------|------|------|
| `invoice.py` | `invoices` / `ocr_results` / `llm_results` / `parsing_diffs` | `database.Base` |
| `audit_log.py` | `audit_logs` 审计日志表 | `database.Base` |

## 说明

- 发票原文件存在 `invoices.file_data`（LargeBinary），不落本地 uploads。
- 改字段时需同步：`schemas/`、前端 `types/`、OCR/LLM 抽取逻辑。
