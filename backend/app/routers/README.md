# backend/app/routers/

HTTP 接口层（薄控制器）。

## 成员

| 文件 | 职责 | 依赖 |
|------|------|------|
| `health.py` | 健康检查（含 DB） | `get_db` |
| `invoices.py` | 上传、列表、详情、文件、导出、差异处理、后台处理任务 | `invoice_service`、`get_db`、models |
| `settings.py` | LLM 配置查询/保存/连通测试 | `config`、`llm_service` |

## 约定

- 路由前缀在 `main.py`：`/api`、`/api/invoices`、`/api/settings`
- 重逻辑放到 `services/`，路由器只做参数校验与调用编排
