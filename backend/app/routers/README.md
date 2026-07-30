# backend/app/routers/

HTTP 接口层（薄控制器）。

## 成员

| 文件 | 职责 | 依赖 |
|------|------|------|
| `health.py` | 健康检查（含 DB） | `get_db` |
| `invoices.py` | 上传、列表、详情、文件、导出、差异处理、后台处理任务 | `invoice_service`、`get_db`、models |
| `batches.py` | 月度批次创建/查询、文档分页、全量 Excel 导出和可重试文档重新处理 | `batch_service`、`get_db`、batch model、openpyxl |
| `settings.py` | LLM 配置查询/保存/连通测试 | `config`、`llm_service` |

## 约定

- 路由前缀在 `main.py`：`/api`、`/api/invoices`、`/api/batches`、`/api/settings`
- 重逻辑放到 `services/`，路由器只做参数校验与调用编排
