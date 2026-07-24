# backend/app/schemas/

Pydantic 请求/响应模型（API 数据形状）。

## 成员

| 文件 | 职责 | 依赖 |
|------|------|------|
| `invoice.py` | 发票上传响应、列表、详情、OCR/LLM/Diff、导出相关 Schema | 被 `routers/invoices.py` 使用 |

## 约定

与 `models/`、前端 `types/invoice.ts` 保持字段语义一致；此处不做数据库写入。
