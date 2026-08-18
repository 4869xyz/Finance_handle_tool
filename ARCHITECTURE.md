# Project Architecture Map

> Invoice Manager / 财务发票处理工具  
> 全局地图：供 Cursor Plan/Agent 与人工复盘使用。业务变更后需同步更新本文件。

---

## 1. 业务定性

- **类型**：前后端分离的 Web 应用（React + FastAPI + PostgreSQL）
- **核心目标**：
  1. 保留单张中国增值税发票 → OCR + Vision LLM 双源识别 → 字段比对/人工审核；
  2. 新增员工月度目录 → 多资料登记/分类/提取 → 批次统计与人工复核。
- **第一阶段范围**：`发票`、`补票`、`支付凭证` 目录；发票、行程/票据、支付/订单截图、境外收据等通用字段。
- **报销提交包（v0.5）**：一次上传 `姓名-报销` 大文件夹（总表 + 多月目录），按月复用现有识别批次，总表右侧新增「系统审核」列。
- **不在当前范围**：视频内容识别、图片相似去重、审批/ERP 对接、专票/普票核对。

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
├── AGENTS.md / CLAUDE.md        # 跨智能体协作规则与 Claude 入口
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
│   │   ├── services/            # OCR / LLM / 单发票与批次编排 / 审计
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
│       ├── pages/               # 单发票上传/列表/详情 + 月度批次页
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
Routers (health / invoices / batches / settings)
    │
    ├─ invoice_service → ocr_service + llm_service + audit_service
    └─ batch_service → document_service → ocr_service + llm_service
                     → document_reconciliation_service → llm_service
                     → package_ingest / claim_completeness（提交包拆月 + 总表审核）
    ▼
Models + Database (PostgreSQL)
```

- **Core 配置层**：`config.py`、`database.py` — 被全后端依赖，勿反向依赖 routers
- **识别层**：`ocr_service` / `llm_service` / `prompts` — 只做识别与解析，不直接碰 HTTP
- **编排层**：`invoice_service` 负责旧单发票流；`batch_service` 负责批次登记、领取、统计、恢复和重试
- **通用文档层**：`document_service` — 类型判定、严格模型响应校验、关键字段矩阵和公开字段映射
- **展示层**：前端 pages/components — 只消费 API，不感知 DB

---

## 6. 主要业务流向 (Data & Logic Flow)

### 6.1 上传与识别

```text
用户拖拽上传 (UploadPage)
  → POST /api/invoices/upload
  → 文件字节写入 invoices.file_data（PostgreSQL）
  → 后台任务 process_invoice：
        OCR(PaddleOCR/规则) + 已配置时的 Vision LLM
        → 写 ocr_results / llm_results / parsing_diffs
        → 更新 invoices 字段与 status
  → 列表/详情刷新展示
```

### 6.2 月度目录登记与识别

```text
用户在 /batches 填员工与月份并选择目录
  → POST /api/batches（files 与 relative_paths 同序配对）
  → 严格路径/扩展名/数量/体积校验，分块计算 SHA-256
  → processing_batches + source_documents 原子登记
  → 特殊规则先行：
       视频 → 人工复核且 file_data=NULL
       PDF合并.pdf → 跳过
       超 5000 万像素图片 → 人工复核且不解码/OCR/LLM
  → 其余文档由 OCR worker 串行领取（配置项 `batch_processing_concurrency` 尚未接入 worker）
  → PDF 全页文本层（保留 pdfplumber 文字坐标）或 PaddleOCR
  → 正文确定性分类；歧义时调用已配置 LLM
  → 按类型分流提取：
       标准发票/出租车票/行程车票 → 本地规则（发票复用 FieldExtractor）优先；关键字段齐全且达阈值则 `RULE_PRIMARY`
       高德行程单 → PDF 坐标表格优先恢复跨行起终点；文本规则次之；再升 LLM
       行程单 RULE_PRIMARY 要求每条 TRIP 均有可用起终点与开始时间，且记录数与“共计 N 单”一致；否则升文本 LLM
       支付/订单截图及杂类 → OCR 文本充足时仅文本 LLM；OCR 稀疏或仍缺关键字段时升级 vision
       LLM 不可用/无效或全部关键字段为空时，整单回退 OCR/Python 规则（`OCR_RULE_FALLBACK`）
  → `_validation.final_source` 区分 `RULE_PRIMARY` / `LLM_TEXT` / `LLM_VISION` / `OCR_RULE_FALLBACK`，并记录 `escalation_path`
  → Prompt 4.3 严格 JSON Schema 仍用于 LLM 路径；规则路径清洗发票 `item_name`、分离支付订单号/交易单号；多行程展示字段按段用 `/` 有序拼接
  → RECOGNIZED / NEEDS_MANUAL_REVIEW / FAILED
  → 批次无活动文档后 COMPLETED
  → Python 生成跨文件重复候选，核对 LLM 判断关系并选择主凭证
  → Python 校验引用、置信度和金额一致性，将 `_reconciliation` 写回原 JSON
```

允许的路径形态为 `发票/文件.pdf` 或 `任意根目录/发票/文件.pdf`，`补票`、`支付凭证` 同理；允许来源目录内继续嵌套，三类来源不要求全部出现。扩展名白名单为 PDF、JPG/JPEG、PNG、MP4/MOV/AVI/MKV。员工与月份来自表单（月份 `YYYY-MM`），不解析不透明根目录。

### 6.6 报销提交包与总表审核

```text
用户在 /batches 选择「姓名-报销」文件夹
  → POST /api/packages（files 与 relative_paths 同序）
  → 识别 *报销总表.xlsx，忽略说明 txt 与月度目录内多余 xlsx
  → 按月度文件夹名拆成多个 processing_batches（复用现有识别）
  → 各月 OCR / 主辅核对完成后自动审核总表
  → GET /api/packages/{id}/export/claims 下载带「系统审核」列的总表副本
```

月度文件夹名支持 `马耿宾2025年4月报销发票` 与 `姓名-2026年1月-报销`。总表「时间」只用于落到年-月。空发票号码不核票号。事项为「打车」「本月打车」时允许该行金额等于当月未占用打车类发票合计。提交人备注列不改。现有单月上传入口保留。

关键不变量：

- 文件名和目录名不能提供日期、金额、对方、号码或单据类型；唯一例外是文件内无法获得有效摘要时，可将去扩展名后的文件名作为 `description` 最后兜底，并记录来源。
- 多笔独立交易写入 `extracted_fields_json.records`；只有每笔金额和原文证据完整时才计算文件级金额。
- 跨文件核对保存主辅关系和建议计入金额供审计；分类汇总直接累加交易级识别金额，不因主辅或疑似关系改变合计。
- 单文档失败不会让正常批次失败；批次 `FAILED` 只表示协调中断或致命失败。
- 启动恢复把遗留的活动文档标为 `PROCESS_INTERRUPTED`，由用户显式重新处理。
- `PROCESS_INTERRUPTED`、`MULTIPLE_TRANSACTIONS` 等是 `status_reason` 原因码，不是处理状态。
- 第一阶段“人工复核”表示可查询的工作队列和原因，不提供批次文档字段编辑/确认 API。

### 6.3 状态流转（简化）

```text
uploaded → processing → confirmed
                     ↘ reviewing（缺关键字段或 OCR/LLM 冲突）
失败重试耗尽 → 回退 uploaded
```

```text
批次：UPLOADED → PROCESSING → COMPLETED
                                 ├─ 文档 RECOGNIZED
                                 ├─ 文档 NEEDS_MANUAL_REVIEW
                                 ├─ 文档 SKIPPED
                                 └─ 文档 FAILED
协调中断：UPLOADED / PROCESSING → FAILED → reprocess
```

### 6.4 配置与健康

```text
LLM 密钥/模型：backend/.env 为唯一真相；页面保存后 API 与 ocr_worker 按文件 mtime 覆盖进程环境并重置 LLM 客户端，下一份文档起生效，无需重建容器。
健康检查：/api/health（含 DB 连通）
```

### 6.5 数据存储事实

- **原始发票文件**：DB `invoices.file_data`（不落本地 uploads 目录）
- **识别结果**：DB 表 `ocr_results` / `llm_results` / `parsing_diffs`
- **月度批次**：DB 表 `processing_batches` / `source_documents`
- **报销提交包**：DB 表 `reimbursement_packages`；`processing_batches.package_id` 可空外键。总表二进制存在提交包上，不复制进每月参考表。
- **批次视频**：仅存路径、大小、哈希和人工复核原因，`source_documents.file_data` 为空
- **其他批次文件**：原始字节存 `source_documents.file_data`；哈希仅审计，不自动去重
- **导出 CSV/Excel**：内存生成后直接下载，不落盘归档
- **交易、核对与审核分类事实**：单文件交易保存在 `source_documents.extracted_fields_json.records`；文件级展示字段 `item_name`、`order_number`、`trip_origin`、`trip_destination`、`trip_time_text` 同时写入表列与 JSON（多值以 `; ` 拼接）；批次主辅关系、主凭证引用、置信度、证据和去重计入金额保存在同一 JSON 的 `_reconciliation`。审核筛选和费用分类结果按交易记录缓存到 `_audit_classification.records`，以源记录哈希、Prompt/策略、公司档案、辅助表和模型配置共同失效。
- **审核及分类汇总**：普通导出优先读取审核分类缓存，只对失效的已识别记录以受限并发调用统一模型服务，不自动补跑跨文件核对；核对缺失时分类审计明细标记“未核对”。工作簿保持 7 张表；“识别结果”一文件一行（含项目名称、订单号、行程起终点与时间列），非 `RECOGNIZED` 行使用浅红底，剔除、两张分类表和分类审计按交易记录一笔一行。分类表按费用组连续排列 A:J 明细，K:N 的申请人、费用类型、详细事项和识别金额合计纵向合并；7 张表的实际使用区域使用细灰边框
- **人民币单币种**：批次资料不提取、不返回、不展示币种，也不按历史币种值拆分汇总；`source_documents.currency` 旧列仅为数据库兼容保留。同组部分金额缺失时合计已有识别金额并标记风险，全部缺失时合计为空
- **分类规则 2.0**：优先顺序为差旅费、交通费、快递费、物流费、项目成本、招待费、职工福利费，办公费和其他明确类型继续自适应；福利类历史名称统一归一为“职工福利费”，“合计”和“会计”属于无效费用类型，辅助表中的无效标签不参与强匹配分类
- **审核分类 Prompt 2.0**：统一模型分别输出审核与分类证据、置信度、证据来源和独立复核标记；Prompt 明确差旅/交通、快递/物流、项目/办公、招待/福利边界，并允许稳定的其他财务类型。Python 确定性规则和辅助表强匹配继续优先，模型复核标记不得覆盖硬排除或规则分类。
- **字段提取分流**：月度资料按类型路由——标准发票/行程车票本地规则优先（`RULE_PRIMARY`）；高德行程单优先用 PDF 原生文字坐标按表头归列并合并跨行单元格，文本规则与 Prompt 4.3 LLM 兜底；行程单直过须每条 TRIP 含可用起终点与开始时间且记录数匹配；支付/订单等 OCR 文本 LLM 优先（`LLM_TEXT`），难例升级 vision（`LLM_VISION`）；合法结果不做字段级规则混补，仅在模型不可用/无效或全部关键字段为空时整单规则兜底。多行程起点/终点/时间按段用 `/` 有序拼接，保留重复地点。

---

## 7. 关键文件速查（按层）

### 后端连接与入口

| 文件 | 作用 |
|------|------|
| `backend/app/database.py` | 创建 DB 引擎、`get_db()` |
| `backend/app/config.py` | 读取 `DATABASE_URL`、LLM、上传限制；从 `.env` 热加载 LLM 键并覆盖进程环境 |
| `backend/app/main.py` | App 入口；启动时 `create_all` 建表 |
| `backend/.env` | 本地/容器运行时密钥与连接串 |

### 后端业务

| 文件 | 作用 |
|------|------|
| `routers/invoices.py` | 上传、列表、详情、文件、导出、差异确认 |
| `routers/batches.py` | 月度批次创建、列表、详情、文档分页、全量 Excel 导出和重处理 |
| `routers/packages.py` | 报销提交包上传、列表、详情、总表审核与带「系统审核」列的总表导出 |
| `routers/settings.py` | LLM 配置、连通测试与原生模型列表；保存后热加载 |
| `routers/health.py` | 健康检查 |
| `services/invoice_service.py` | 处理编排、字段合并、待审核判定 |
| `services/batch_service.py` | 路径校验、流式登记、特殊规则、领取、统计、恢复和重试 |
| `services/package_ingest_service.py` | 提交包路径拆分、总表解析登记、按月创建批次 |
| `services/claim_workbook_service.py` | 报销总表读写（系统审核列） |
| `services/claim_completeness_service.py` | 总表明细与识别结果缺件/分类核验 |
| `services/document_service.py` | 通用分类、按类型分流提取（规则/文本 LLM/vision）、关键字段校验与公开字段映射 |
| `services/document_prompts.py` | 多资料分类/提取 Prompt |
| `services/expense_record_service.py` | 把文件级 JSON 转为下游一笔一行的交易视图，兼容历史数据 |
| `services/audit_classification_service.py` | 审核筛选与费用分类的统一模型批处理、记录级缓存、失效判断、重试和耗时指标 |
| `services/document_reconciliation_service.py` | 跨文件候选生成、LLM 主辅关系判断、Python 裁决和 JSON 持久化 |
| `services/ocr_service.py` | PaddleOCR + 增值税字段规则抽取 |
| `services/llm_service.py` | 多厂商 Vision 调用与 JSON 解析 |
| `services/prompts.py` | 中国发票 Vision Prompt / Schema |
| `models/invoice.py` | invoices / ocr_results / llm_results / parsing_diffs |
| `models/batch.py` | processing_batches / source_documents / 批次枚举 |
| `models/reimbursement_package.py` | reimbursement_packages 提交包账本 |
| `models/audit_log.py` | 审计日志表 |

### 前端

| 文件 | 作用 |
|------|------|
| `src/App.tsx` | 路由：上传 / 列表 / 详情 / 月度资料 |
| `src/pages/UploadPage.tsx` | 上传页 |
| `src/pages/InvoiceListPage.tsx` | 列表 + 统计 + 筛选导出 |
| `src/pages/InvoiceDetailPage.tsx` | 详情 + OCR/LLM 差异对比 |
| `src/pages/BatchPage.tsx` | 目录上传、批次统计、轮询、文档分页明细和终态批次 Excel 导出 |
| `src/services/api.ts` | 封装 REST 调用 |
| `src/types/invoice.ts` | 前端类型定义 |
| `src/types/batch.ts` | 批次/文档类型与中文状态、原因映射 |

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
| `docs/交接文档.md` | 离职/接手总览（启动、产品线、红线、排障） |
| 各目录 `@README.md` | 目录成员清单 |
| `docs/plans/{Feature}_plan.md` | 单功能唯一计划源 |
| `VersionLog.md` | 按自然日合并的变更日志 |
| `.cursorrules` | Plan / Agent 模式 SOP |

功能开发流程详见：`docs/Cursor项目规范使用流程.md`。
