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
| `tests/` | 旧发票回归及批次路径、上传、短路、识别、状态和可选 PostgreSQL 集成测试 | `app/services` |

`LLM_BATCH_CONCURRENCY` 控制审核及分类导出的模型批处理并发数，默认 `2`；提高该值可能受模型供应商限流影响。

## 启动

通常由根目录 `docker-compose.yml` 的 `backend` 服务启动：`uvicorn app.main:app --port 18080`。

测试：

```powershell
python -m pytest -q tests
# 配置 TEST_DATABASE_URL 后执行 PostgreSQL 并发领取测试
python -m pytest -q -m postgres_integration tests
```
