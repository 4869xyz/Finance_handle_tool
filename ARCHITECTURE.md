# Project Architecture Map

> Invoice Manager / 财务发票处理工具  
> 全局地图：供 Cursor Plan/Agent 与人工复盘使用。业务变更后需同步更新本文件。

---

## 1. 业务定性

- **类型**：前后端分离的 Web 应用（React + FastAPI + PostgreSQL）
- **核心目标**：上传中国增值税发票 → OCR + Vision LLM 双源识别 → 字段比对/人工审核 → 列表管理与导出
- **不在范围内（当前）**：行程单、火车票、酒店水单等非增值税发票专用识别

---

## 2. 技术栈 (Tech Stack)

| 层 | 技术 |
|----|------|
| 前端 | React 18、TypeScript、Vite、Ant Design |
| 后端 | Python 3.11、FastAPI、SQLAlchemy（异步）、Uvicorn |
| 数据库 | PostgreSQL 16 |
| OCR | PaddleOCR + pdfplumber / pdf2image |
| LLM | 多厂商 Vision API（OpenAI / Anthropic / Google / Qwen / DeepSeek / Zhipu） |
| 部署 | Docker Compose（db + backend + frontend） |

---

## 3. 全局文件结构树 (Project Tree)

```text
Finance_handle_tool/
├── ARCHITECTURE.md              # 本文件：全局架构地图
├── VersionLog.md                # 版本/变更日志（按自然日合并）
├── .cursorrules                 # Cursor Plan/Agent 协作约束
├── .cursorignore                # 减少 Cursor 上下文噪声
├── docker-compose.yml           # 一键编排 db/backend/frontend
├── README.md                    # 项目对外说明与快速开始
├── caozuo.md                    # 本地运行备忘（端口与常用命令）
├── AGENTS.md / CLAUDE.md        # OpenSpec / Agent 入口提示（与 Cursor 流程并存）
│
├── backend/                     # FastAPI 后端
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── .env / .env.example
│   ├── app/
│   │   ├── main.py              # FastAPI 入口、CORS、启动建表
│   │   ├── config.py            # 配置（DB、LLM、上传限制等）
│   │   ├── database.py          # 数据库引擎与 get_db()
│   │   ├── rate_limit.py        # 接口限流
│   │   ├── models/              # SQLAlchemy 表模型
│   │   ├── schemas/             # Pydantic 请求/响应模型
│   │   ├── routers/             # HTTP 路由
│   │   ├── services/            # 业务：OCR / LLM / 发票编排 / 审计
│   │   └── repositories/        # 数据访问层占位（当前较薄）
│   └── tests/
│
├── frontend/                    # React 前端
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts           # 端口 15173、/api 代理到 backend
│   ├── index.html
│   └── src/
│       ├── main.tsx / App.tsx
│       ├── pages/               # 上传 / 列表 / 详情页
│       ├── components/          # 布局、仪表盘、状态标签等
│       ├── services/api.ts      # 调用后端 API
│       ├── hooks/ / types/ / styles/
│
├── docs/                        # 设计与学习文档
│   ├── plans/                   # 功能 plan（Cursor 工作流）
│   ├── DOCKER_学习复盘.md
│   ├── UI_REDESIGN.md
│   └── ...
│
└── scripts/
    └── dev.sh                   # 本地开发辅助脚本
```

---

## 4. 目录职责 (Directory Responsibility)

| 目录 | 职责 | 约束 |
|------|------|------|
| `backend/app/models` | 数据库表结构定义 | 改字段需同步 schema / 前端 types / 迁移意识 |
| `backend/app/schemas` | API 入参出参校验 | 与 models 字段对齐，不写业务副作用 |
| `backend/app/routers` | HTTP 接口层 | 薄控制器：校验 + 调 service + 返回 |
| `backend/app/services` | 核心业务逻辑 | OCR/LLM/比对/审计；重逻辑放这里 |
| `backend/app/repositories` | 数据访问抽象 | 当前使用较少，新增复杂查询可放此 |
| `frontend/src/pages` | 页面级 UI | 页面编排，复杂逻辑抽 hooks/components |
| `frontend/src/components` | 可复用组件 | 不直接写死业务 API 路径（经 services） |
| `frontend/src/services` | 前端 API 客户端 | 唯一与后端通信出口 |
| `docs/plans` | 功能计划书 | 单一功能一个 `*_plan.md`；由 Cursor 维护 |
| `docs`（除 plans） | 设计/学习文档 | 勿把临时 plan 混进全局搜索（见 `.cursorignore`） |

---

## 5. 模块划分 (Module Breakdown)

```text
Frontend (UI)
    │  HTTP /api/*
    ▼
Routers (health / invoices / settings)
    │
    ▼
Services (invoice_service 编排 → ocr_service + llm_service + audit_service)
    │
    ▼
Models + Database (PostgreSQL)
```

- **Core 配置层**：`config.py`、`database.py` — 被全后端依赖，勿反向依赖 routers
- **识别层**：`ocr_service` / `llm_service` / `prompts` — 只做识别与解析，不直接碰 HTTP
- **编排层**：`invoice_service` — 上传后处理、字段合并、状态流转
- **展示层**：前端 pages/components — 只消费 API，不感知 DB

---

## 6. 主要业务流向 (Data & Logic Flow)

### 6.1 上传与识别

```text
用户拖拽上传 (UploadPage)
  → POST /api/invoices/upload
  → 文件字节写入 invoices.file_data（PostgreSQL）
  → 后台任务 process_invoice：
        并行 OCR(PaddleOCR/规则) + Vision LLM
        → 写 ocr_results / llm_results / parsing_diffs
        → 更新 invoices 字段与 status
  → 列表/详情刷新展示
```

### 6.2 状态流转（简化）

```text
uploaded → processing → confirmed
                     ↘ reviewing（缺关键字段或 OCR/LLM 冲突）
失败重试耗尽 → 回退 uploaded
```

### 6.3 配置与健康

```text
LLM 密钥/模型：backend/.env + /api/settings
健康检查：/api/health（含 DB 连通）
```

### 6.4 数据存储事实

- **原始发票文件**：DB `invoices.file_data`（不落本地 uploads 目录）
- **识别结果**：DB 表 `ocr_results` / `llm_results` / `parsing_diffs`
- **导出 CSV/Excel**：内存生成后直接下载，不落盘归档

---

## 7. 关键文件速查（按层）

### 后端连接与入口

| 文件 | 作用 |
|------|------|
| `backend/app/database.py` | 创建 DB 引擎、`get_db()` |
| `backend/app/config.py` | 读取 `DATABASE_URL`、LLM、上传限制 |
| `backend/app/main.py` | App 入口；启动时 `create_all` 建表 |
| `backend/.env` | 本地/容器运行时密钥与连接串 |

### 后端业务

| 文件 | 作用 |
|------|------|
| `routers/invoices.py` | 上传、列表、详情、文件、导出、差异确认 |
| `routers/settings.py` | LLM 配置与连通测试 |
| `routers/health.py` | 健康检查 |
| `services/invoice_service.py` | 处理编排、字段合并、待审核判定 |
| `services/ocr_service.py` | PaddleOCR + 增值税字段规则抽取 |
| `services/llm_service.py` | 多厂商 Vision 调用与 JSON 解析 |
| `services/prompts.py` | 中国发票 Vision Prompt / Schema |
| `models/invoice.py` | invoices / ocr_results / llm_results / parsing_diffs |
| `models/audit_log.py` | 审计日志表 |

### 前端

| 文件 | 作用 |
|------|------|
| `src/App.tsx` | 路由：上传 / 列表 / 详情 |
| `src/pages/UploadPage.tsx` | 上传页 |
| `src/pages/InvoiceListPage.tsx` | 列表 + 统计 + 筛选导出 |
| `src/pages/InvoiceDetailPage.tsx` | 详情 + OCR/LLM 差异对比 |
| `src/services/api.ts` | 封装 REST 调用 |
| `src/types/invoice.ts` | 前端类型定义 |

### 基础设施

| 文件 | 作用 |
|------|------|
| `docker-compose.yml` | db:5435、backend:18080、frontend:15173 |
| `backend/Dockerfile` / `frontend/Dockerfile` | 镜像构建 |
| `frontend/vite.config.ts` | 开发服与 `/api` 代理 |

---

## 8. 运行入口（操作速查）

```powershell
# 项目根目录
docker compose up -d
docker compose ps
docker compose logs -f
```

- 前端：http://localhost:15173  
- 后端文档：http://localhost:18080/docs  
- 数据库：localhost:5435（容器内 5432）

更细命令见 `caozuo.md`。

---

## 9. Cursor 协作锚点

| 文档 | 用途 |
|------|------|
| `@ARCHITECTURE.md` | 全局模块与数据流（本文件） |
| 各目录 `@README.md` | 目录成员清单 |
| `docs/plans/{Feature}_plan.md` | 单功能唯一计划源 |
| `VersionLog.md` | 按自然日合并的变更日志 |
| `.cursorrules` | Plan / Agent 模式 SOP |

功能开发流程详见：`docs/Cursor项目规范使用流程.md`。
