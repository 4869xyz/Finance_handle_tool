---
title: "Invoice Manager 项目规范：Cursor 流程"
source: "local-project"
created: "2026-07-24"
description: "对照 Unity v2 Cursor 流程，适配本仓库（React+FastAPI+PostgreSQL）：深度扫描话术、ARCHITECTURE/README、.cursorignore/.cursorrules、Plan/Agent 三阶段，以及日常启动操作。"
tags: [cursor, plan-mode, agent-mode, fastapi, react, docker, invoice, architecture, version-log]
---

## Summary

本稿面向 **Invoice Manager（财务发票处理工具）+ Cursor** 的模式化协作：

- 用 `.cursorignore`（含 `docs/plans/*.md`）减少上下文污染
- 用 `.cursorrules` 区分 **Plan 模式**（只维护 plan、禁止提前改业务代码）与 **Agent 模式**（严格按 Checklist 改文件并同步 VersionLog / ARCHITECTURE / README）
- 用 `ARCHITECTURE.md` + 各目录 `README.md` 作为 AI/人工导航地图
- 附带本项目日常 **Docker 启动与使用**说明

模板来源：`v2 任何程序项目规范使用Cursor的流程.md`（已按本仓库技术栈改写）。

---

# 深度扫描项目

> 我是一个全栈/财务工具开发新手，现在需要你作为**“资深全栈架构师”**来接管这个项目。  
> 请通过 @ARCHITECTURE.md 与关键目录深度扫描当前项目（重点：`backend/app`、`frontend/src`、`docker-compose.yml`）。  
> 请用通俗易懂的中文回答我：  
> 1. 业务定性：这是一个什么类型的项目？核心业务目标是什么？  
> 2. 核心数据流：用户上传发票后，数据如何在前端 → 后端 → 数据库 → 识别服务间流动？  
> 3. 页面/接口地图：梳理目前已有的主要功能。

---

# 自动生成 / 维护文件

> **ARCHITECTURE.md：**既然你已完全理解本项目，请在 @ARCHITECTURE.md 中详细记录：根目录全局地图、文件树、模块划分与数据流、配置结构等。

> **README.md：**在重要目录下维护对应的 `README.md`，记录该目录每个文件的职责与依赖关系（见下方已建立的目录清单）。

> **VersionLog.md：**业务代码/配置/规则变更后，按自然日 `YYYY-MM-DD` 追加条目。

---

# 项目建设（已按本规范落地）

## A. 建立 `.cursorignore`

根目录 `.cursorignore` 已创建，作用：避免把锁文件、构建产物、临时 plan 全文灌进全局检索。

关键规则包括：

- `**/node_modules/`、`**/__pycache__/`、`.venv/`
- `package-lock.json`、大图/设计源文件（按需）
- `docs/plans/*.md`（防止 plan 污染全局搜索；需要时用 `@docs/plans/xxx_plan.md` 显式引用）

## B. 建立约束 `.cursorrules`

根目录 `.cursorrules` 已创建。这是 Cursor 的“种子”，对话中应被遵循。

核心约定：

| 模式 | 行为 |
|------|------|
| Plan | 只维护 `docs/plans/{Feature}_plan.md`，切换 Agent 前禁止改业务代码 |
| Agent | 严格按 Checklist 改列出的文件；完成后同步 plan / ARCHITECTURE / README / VersionLog |

详细条文见 `.cursorrules`。

## C. 建立全局地图与成员清单

1. **根目录 `ARCHITECTURE.md`**：全局文件树、技术栈、模块边界、数据流、关键文件速查  
2. **重要目录 `README.md`**：该目录文件职责与依赖  
3. **`VersionLog.md`**：按日合并的变更日志  
4. **`docs/plans/`**：功能计划书目录  

---

# 日常项目操作使用（非 Cursor）

## 1. 启动服务

在项目根目录：

```powershell
docker compose up -d
```

查看状态 / 日志：

```powershell
docker compose ps
docker compose logs -f
```

## 2. 访问地址

| 服务 | 地址 |
|------|------|
| 前端 | http://localhost:15173 |
| 后端 Swagger | http://localhost:18080/docs |
| 数据库 | localhost:5435（用户/库见 compose 与 `.env`） |

备忘也可看根目录 `caozuo.md`。

## 3. 首次配置

1. 复制 `backend/.env.example` → `backend/.env`（若尚未有）  
2. 配置 `DATABASE_URL` 与 LLM 相关 Key（如 Qwen / OpenAI）  
3. `docker compose up -d`  
4. 打开前端 → 设置 LLM → 上传增值税发票试用  

## 4. 数据在哪

- 上传的发票文件：PostgreSQL `invoices.file_data`（不是本地 uploads 文件夹）  
- 识别结果：`ocr_results` / `llm_results` / `parsing_diffs`  
- 物理存储：Docker 卷 `postgres_data`  

## 5. 能力边界（避免误解）

- **支持**：中国增值税发票（纸质/电子/全电等字段体系）  
- **技术**：PaddleOCR + 规则抽取 + Vision LLM 双源比对  
- **不支持（产品级）**：行程单、火车票、酒店水单等专用识别  

---

# 后续使用（需求处理 · Cursor）

## 第一步：需求分析 SOP（Plan 模式）

```Markdown
@.cursorrules 需求分析 SOP：
```

**期望行为**：检索 `@ARCHITECTURE.md` → 识别受影响模块 → 在 `docs/plans/xxx_plan.md` 生成需求/Checklist → 抛出 2–3 个确认问题。

## 第二步：需求细化 SOP（Plan 模式）

若方案未定或需补资料：

```Markdown
@.cursorrules 需求细化 SOP，维护当前plan.md： @docs/plans/xxx_plan.md
```

**期望行为**：继续答疑 → **实时更新** plan → 标注 `[Conflict]`（如有）→ 再次抛出确认问题。

## 第三步：完成计划 SOP（Agent 模式）

```Markdown
@.cursorrules 完成计划 SOP: @docs/plans/xxx_plan.md
```

**期望行为**：按 Checklist 逐项改代码 → 每完成一项勾选 → 最后更新 `VersionLog.md`、受影响 `README.md`、必要时 `ARCHITECTURE.md`。

## 定期优化结构

```Markdown
我手动新增了一些文件和目录层级，你重新检索后，补充 ARCHITECTURE.md，并修改对应被改动目录的 README.md，不修改其他内容。
```

---

# 目录 / 文件作用总表（速查）

更完整说明以 `ARCHITECTURE.md` 与各目录 `README.md` 为准。摘要如下：

### 根目录

| 路径 | 作用 |
|------|------|
| `docker-compose.yml` | 编排 Postgres / 后端 / 前端 |
| `ARCHITECTURE.md` | 全局架构地图 |
| `VersionLog.md` | 变更日志 |
| `.cursorrules` | Cursor 协作 SOP |
| `.cursorignore` | Cursor 忽略列表 |
| `README.md` | 对外项目说明 |
| `caozuo.md` | 启动与端口备忘 |

### backend

| 路径 | 作用 |
|------|------|
| `app/main.py` | FastAPI 入口 |
| `app/database.py` | **数据库连接** |
| `app/config.py` | 环境配置 |
| `app/models/` | 表结构 |
| `app/schemas/` | API 数据形状 |
| `app/routers/` | HTTP 接口 |
| `app/services/` | OCR/LLM/发票处理逻辑 |
| `Dockerfile` / `requirements.txt` | 镜像与 Python 依赖 |
| `.env` | 密钥与 DATABASE_URL（勿提交真实密钥） |

### frontend

| 路径 | 作用 |
|------|------|
| `src/pages/` | 上传、列表、详情页 |
| `src/components/` | 布局、统计卡、状态标签等 |
| `src/services/api.ts` | 调后端 |
| `vite.config.ts` | 开发服务器与代理 |
| `Dockerfile` / `package.json` | 镜像与 Node 依赖 |

### docs

| 路径 | 作用 |
|------|------|
| `plans/` | 功能 `{Feature}_plan.md` |
| `DOCKER_学习复盘.md` | Docker 概念学习笔记 |
| `UI_REDESIGN.md` 等 | 历史设计记录 |

---

# Plan 文件建议模板

`docs/plans/{Feature}_plan.md` 建议包含：

```markdown
# {Feature} Plan

## 背景
## 目标 / 非目标
## 受影响文件（预估路径）
## Checklist
- [ ] ...
## 风险 / Conflict
## 确认问题
1. ...
## 状态
draft | confirmed | in_progress | done
```

Checklist 每一项必须**原子化**，Agent 可在不问人的情况下独立完成。
