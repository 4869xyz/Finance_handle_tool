# backend/app/

FastAPI 应用包。

## 成员

| 文件 | 职责 | 依赖 |
|------|------|------|
| `main.py` | 应用入口：CORS、路由挂载、启动建表与批次中断恢复 | `config`、`database`、`routers/*` |
| `config.py` | Settings：DB、LLM、上传限制；`.env` LLM 键热加载并覆盖进程环境 | `.env` / 环境变量 |
| `database.py` | **数据库连接**：engine、`get_db()`、`Base` | `config.database_url` |
| `rate_limit.py` | API 限流 | slowapi |
| `models/` | ORM 表 | 见该目录 README |
| `schemas/` | Pydantic 入参/出参 | 被 routers 使用 |
| `routers/` | HTTP 路由 | services + get_db |
| `services/` | 业务逻辑（OCR/LLM/编排） | models、外部 API |
| `repositories/` | 数据访问层占位 | 当前较薄 |

## 数据流

`routers` → `services` → `models` / DB（经 `database.get_db`）。旧单发票流与月度批次流使用独立模型和编排服务，共享 OCR/LLM 基础能力。
