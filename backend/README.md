# backend/

后端服务根目录（FastAPI + OCR/LLM）。

## 成员

| 路径 | 职责 | 依赖 |
|------|------|------|
| `Dockerfile` | 构建后端镜像（Python、系统库、PaddleOCR 依赖） | `requirements.txt` |
| `requirements.txt` | Python 依赖清单 | 被 Dockerfile / 本地 pip 使用 |
| `.env` | 运行时密钥与 `DATABASE_URL`（本地，勿提交真实密钥） | `app/config.py` |
| `.env.example` | 环境变量模板 | 新人复制为 `.env` |
| `app/` | 应用源码 | 见 `app/README.md` |
| `tests/` | 单元/解析相关测试 | `app/services` |

## 启动

通常由根目录 `docker-compose.yml` 的 `backend` 服务启动：`uvicorn app.main:app --port 18080`。
