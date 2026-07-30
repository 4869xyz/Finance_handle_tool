<div align="center">

# Invoice Manager 🧾

### AI-Powered Invoice and Monthly Reimbursement Document Recognition
### AI驱动的发票与月度报销资料识别 · OCR+LLM双流程

**v0.4.0** | Latest Release: 2026-07-25

[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18.2-61DAFB.svg)](https://reactjs.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![PaddleOCR](https://img.shields.io/badge/PaddleOCR-2.7-0052CC.svg)](https://github.com/PaddlePaddle/PaddleOCR)

**[English](#-highlights) | [简体中文](#-亮点)**

<br/>

> 🎯 **不只是OCR，是让AI帮你双重检查每一张发票**
>
> 传统OCR识别率只有70-80%，商业方案价格昂贵。Invoice Manager 使用 **OCR + LLM 双源验证**，
> 让两个AI互相校验，准确率大幅提升，而且完全免费开源！

</div>

---

## ✨ Highlights

- **🔍 Dual-Source Verification** - OCR + LLM double-check every field, conflicts auto-flagged for review
- **🤖 Multi-LLM Support** - GPT-4o / Claude / Gemini / Qwen / DeepSeek / Zhipu GLM
- **🇨🇳 Chinese Invoice Optimized** - PaddleOCR + custom prompts for VAT invoices (增值税发票)
- **⚡ Parallel Processing** - OCR and LLM run simultaneously for faster results
- **🐳 One-Click Deploy** - Docker Compose, ready in 3 minutes
- **🔒 Self-Hosted Ledger** - Files stay in your PostgreSQL; configured LLM calls follow that provider's data policy

## ✨ 亮点

- **📁 月度目录识别** - 按员工和报销月份批量登记发票、补票、支付凭证，统一查看进度与人工复核项
- **🧾 多资料类型** - 支持电子/增值税/出租车发票、行程单、票据、支付/订单截图、境外收据等
- **🛡️ 确定性短路** - 视频、精确命名 `PDF合并.pdf`、超像素长图在 OCR/LLM 前安全分流并保留原因
- **🔍 双源验证** - OCR + LLM 双重识别，字段冲突自动标记待审核
- **🤖 多模型支持** - 支持 GPT-4o / Claude / Gemini / 通义千问 / DeepSeek / 智谱GLM
- **🇨🇳 中文发票优化** - PaddleOCR 深度优化 + 增值税发票专用提示词
- **⚡ 并行处理** - OCR 和 LLM 同时运行，识别更快
- **🐳 一键部署** - Docker Compose 启动，3分钟搞定
- **🔒 自托管账本** - 文件存于自己的 PostgreSQL；配置 LLM 后，调用内容受所选提供商的数据政策约束

---

## 📦 版本更新 | Changelog

### v0.4.0 (2026-07-25) - 月度多资料识别第一阶段

- 新增 `/batches` 月度资料工作台和 `/api/batches` 批次 API，不改变原 `/api/invoices/*` 单发票流程。
- 支持浏览器目录上传并严格校验 `发票`、`补票`、`支付凭证` 来源路径、文件配对、数量和体积。
- 新增批次/来源文档账本、SHA-256、处理状态、原因码、原始模型响应和结构化字段。
- PDF 使用全页文本层或 OCR；多类型字段由规则和可选 LLM 严格 JSON Schema 提取。
- 视频仅存元数据；`PDF合并.pdf` 跳过；超 5000 万像素图片进入人工复核。
- 支持批次统计、2 秒前端轮询、中断恢复和可重试文档重新处理。

### v0.3.0 (2026-01-24) - UI 全面改版 | UI Redesign

**🎨 全新设计系统 | New Design System**
- 采用 Swiss Clean Web Dashboard 设计风格，专业简洁
- 全新配色方案：朱红色品牌色 (#E42313) + 清爽白灰色调
- 字体升级：Space Grotesk (标题) + Inter (正文)
- 统一的圆角、边框、间距设计规范

**🧩 新增组件 | New Components**
- **Sidebar 侧边栏导航** - 替代顶部导航，提供更清晰的页面结构
- **MetricCard 统计卡片** - 发票列表页顶部显示关键统计数据
- **StatusTag 状态标签** - 统一的状态展示组件 (待审核/处理中/已确认等)
- **ControlBar 控制栏** - 搜索、筛选、操作按钮的统一布局

**📄 页面重构 | Page Redesign**
- **发票列表页** - 全新布局，新增4个统计卡片展示发票数量、金额合计、税额合计、价税合计
- **发票详情页** - 双栏布局 (数据卡片 + PDF预览)，**OCR vs LLM 差异可视化高亮** (红色背景标记不匹配字段)
- **上传页面** - 简洁的拖拽上传区域，品牌化设计

**✨ 关键 UX 改进 | Key UX Improvements**
- **差异可视化** - OCR 与 LLM 识别结果对比表，不匹配字段自动红色高亮
- **匹配计数器** - "X/Y 字段匹配" 直观显示识别一致性
- **状态图标** - ✓/✗ 图标快速识别匹配状态
- **更好的层级** - 卡片式设计，信息分组更清晰

**⚙️ 后端增强 | Backend Improvements**
- OpenRouter 模型支持 - 接入更多最新 AI 模型
- 审计日志 - 记录关键操作
- 速率限制 - 防止 API 滥用
- OCR 服务优化 - 更好的中文发票识别
- 改进的 LLM 提示词 - 更精准的发票信息提取

**🎯 设计文件 | Design Files**
- 新增 `docs/invoice-manager.pen` - 完整的 UI 设计稿 (Pencil 格式)
- 新增 `docs/UI_REDESIGN.md` - 设计规范文档

---

### v0.2.0 - 多LLM支持 + 双源验证

- 支持 6 种 LLM 提供商 (OpenAI, Anthropic, Google, 阿里云, DeepSeek, 智谱)
- OCR + LLM 双源验证机制
- 冲突检测与人工复核流程

### v0.1.0 - 基础版本

- PaddleOCR 中文发票识别
- 基础的发票管理 CRUD
- Docker 一键部署

---

## 🖼️ 截图预览 | Screenshots

<div align="center">

### 发票详情 & OCR vs LLM 比对 | Invoice Detail & Dual-Source Comparison

![Invoice Detail](docs/images/invoice-detail.png)

*左侧：发票基础信息和原始文件预览 | 下方：OCR与LLM识别结果对比，可一键选择或自定义*

</div>

**截图亮点 | Screenshot Highlights:**
- 📋 **发票信息卡片** - 清晰展示所有提取字段
- 📄 **原始文件预览** - PDF/图片内嵌预览，无需下载
- ⚖️ **OCR vs LLM 比对表** - 并排显示两种识别结果
- 🏷️ **状态标签** - 已解决/待确认，一目了然
- 🖱️ **一键选择** - 点击"选择"采用该结果，或"自定义"手动输入

---

### 列表页面

![Invoice List](docs/images/invoice-list.png)

### 上传页面

![Invoice Upload](docs/images/invoice-upload.png)

## 🎯 解决的问题 | Problem Solved

<table>
<tr>
<td width="50%">

### 😫 传统方式的痛点

- ❌ 手动录入发票信息，费时费力
- ❌ 单一OCR识别率不够高（70-80%）
- ❌ 商业方案价格昂贵（按张收费）
- ❌ 现有开源方案功能单一
- ❌ 数据上传第三方，隐私风险

</td>
<td width="50%">

### 😊 Invoice Manager 的解决方案

- ✅ 自动识别，批量上传处理
- ✅ 双源验证，准确率显著提升
- ✅ 完全免费，MIT 开源协议
- ✅ 全流程管理，从识别到报销
- ✅ 本地部署，数据完全可控

</td>
</tr>
</table>

---

## 🏗️ 核心架构 | Architecture

```
单发票流程：/upload → /api/invoices → OCR + 可选 Vision LLM → Diff → 确认/复核

月度资料流程：/batches → /api/batches → 路径与特殊规则
                                      → 通用 PDF/OCR → 类型分类 → 严格字段提取
                                      → 已识别 / 人工复核 / 跳过 / 失败
```

**Why Dual-Source Verification Works | 为什么双源验证更准确**

| 场景 | 单一OCR | 单一LLM | 双源验证 |
|-----|--------|---------|---------|
| 模糊字符 | ❌ 可能误识别 | ✅ 理解上下文 | ✅ 互相校验 |
| 非标准格式 | ❌ 正则失效 | ✅ 智能解析 | ✅ 双重保障 |
| 金额数字 | ✅ 精确识别 | ⚠️ 可能幻觉 | ✅ 数值比对 |
| 处理成本 | 💰 低 | 💰💰 高 | 💰 按需调用 |

---

## 🚀 快速开始 | Quick Start

### 最简3步启动 | 3 Steps to Start

```powershell
# 1. 在项目根目录复制配置模板
Copy-Item backend/.env.example backend/.env

# 2. 检查 backend/.env 的 DATABASE_URL；LLM API Key 可留空
#    使用外部 LLM 时再填入对应提供商的 Key

# 3. 启动服务 | Start Services
docker compose up -d
docker compose ps
```

### 访问地址 | Access URLs

| 服务 | 地址 | 说明 |
|-----|------|------|
| 前端界面 | http://localhost:15173 | 主操作界面 |
| 后端 API | http://localhost:18080 | REST API |
| API 文档 | http://localhost:18080/docs | Swagger UI |

### 配置 LLM | Configure LLM

系统支持 **6 种 LLM 提供商**，可在界面中实时切换：

| Provider | Models | 获取 API Key |
|----------|--------|-------------|
| OpenAI | GPT-4o, GPT-4o-mini | [platform.openai.com](https://platform.openai.com/) |
| Anthropic | Claude 3.5 Sonnet, Claude 3 Opus | [console.anthropic.com](https://console.anthropic.com/) |
| Google | Gemini 1.5 Pro, Gemini 1.5 Flash | [aistudio.google.com](https://aistudio.google.com/) |
| 阿里云 | 通义千问 Qwen-VL | [dashscope.console.aliyun.com](https://dashscope.console.aliyun.com/) |
| DeepSeek | DeepSeek-VL | [platform.deepseek.com](https://platform.deepseek.com/) |
| 智谱 | GLM-4V | [open.bigmodel.cn](https://open.bigmodel.cn/) |

> 💡 **提示**：LLM 可选。旧单发票流会跳过 Vision 分支；月度资料流可由确定性规则自动通过字段完整且达到阈值的文档，其余进入人工复核。配置 LLM 后才会把相关文本或图像发送给所选提供商。

---

## 📋 功能特性 | Features

### 发票处理 | Invoice Processing

| 功能 | 描述 |
|-----|------|
| 📤 批量上传 | 支持 PDF、JPG、PNG，单次多文件上传 |
| 🔍 OCR识别 | PaddleOCR 中文优化，300DPI 高清识别 |
| 🤖 LLM解析 | Vision 模型直接理解发票图像 |
| ⚖️ 智能比对 | 自动对比OCR和LLM结果，标记差异 |
| ✏️ 人工复核 | 冲突字段可视化，一键选择正确值 |
| 📊 统计报表 | 金额汇总、税额统计、状态分布 |

### 月度资料处理 | Monthly Document Processing

| 功能 | 描述 |
|-----|------|
| 📁 目录上传 | 选择含 `发票`、`补票`、`支付凭证` 的月度目录；三者可缺省，允许安全嵌套；员工和月份由表单提供 |
| 🔐 原子登记 | 最多 200 个文件、单文件 150 MiB、单批 256 MiB；失败时整批不落库 |
| 🧭 多类型识别 | 发票、行程、票据、支付、订单、境外收据和其他报销资料 |
| 👀 人工复核队列 | 缺关键字段、低置信度、多笔无汇总、视频和超像素图片均给出原因；第一阶段只查询/重试，不提供批次字段编辑或确认 API |
| 🔁 恢复与重试 | 服务中断可审计，人工复核/失败文档可选择性重新处理 |
| 📥 批次导出 | 处理完成后可导出该批次全部来源文档的 Excel 识别结果（包括已识别、人工复核、跳过和失败记录） |

### 提取字段 | Extracted Fields

```
├── 基础信息
│   ├── 发票号码 (invoice_number)
│   ├── 开票日期 (issue_date)
│   └── 项目名称 (item_name)
│
├── 购买方信息
│   ├── 购买方名称 (buyer_name)
│   └── 购买方税号 (buyer_tax_id)
│
├── 销售方信息
│   ├── 销售方名称 (seller_name)
│   └── 销售方税号 (seller_tax_id)
│
└── 金额信息
    ├── 金额 (amount)
    ├── 税率 (tax_rate)
    ├── 税额 (tax_amount)
    └── 价税合计 (total_with_tax)
```

月度资料公开字段按类型统一为：`document_date`、`counterparty`、`description`、`amount`、`invoice_number`、`transaction_number`；完整候选字段和多笔交易保存在 `extracted_fields_json`。批次资料统一按人民币处理，不提取或返回币种；金额在 API 中以十进制字符串返回。

其中 `description` 是批次资料的“摘要”字段：模型可用时，模型仅根据 OCR/PDF 正文生成交易或资料摘要；模型不可用或字段提取失败时，系统以 OCR 文本的第一条非空行作为降级摘要（最多 500 字符）。文件名和目录名不参与摘要生成。摘要为空不会单独阻止发票类资料进入已识别状态。

### 状态流转 | Status Workflow

旧单发票状态：

```
UPLOADED → PROCESSING → REVIEWING → CONFIRMED → REIMBURSED
  上传       识别中       待审核       已确认       已报销
                            ↓
                      NOT_REIMBURSED
                         未报销
```

月度批次与文档状态：

```text
批次：UPLOADED → PROCESSING → COMPLETED
                      中断/致命错误 → FAILED

文档：UPLOADED → PROCESSING → RECOGNIZED
                          ├→ NEEDS_MANUAL_REVIEW
                          └→ FAILED
特殊规则直接进入 NEEDS_MANUAL_REVIEW 或 SKIPPED
```

`PROCESS_INTERRUPTED` 等值是原因码，不是处理状态。

---

## 📡 API 接口 | API Reference

### 核心接口 | Core Endpoints

```bash
# 上传发票 | Upload Invoice
POST /api/invoices/upload
Content-Type: multipart/form-data

# 获取列表 | Get List (with pagination & filters)
GET /api/invoices?status=REVIEWING&owner=张三&page=1&page_size=20

# 获取详情 | Get Detail (includes OCR, LLM, Diff results)
GET /api/invoices/{id}

# 原始文件、删除、确认和差异确认
GET    /api/invoices/{id}/file
DELETE /api/invoices/{id}
POST   /api/invoices/{id}/confirm
POST   /api/invoices/{id}/diffs/{diff_id}/resolve

# 更新信息 | Update Invoice
PUT /api/invoices/{id}
{
  "invoice_number": "12345678",
  "total_with_tax": "1000.00",
  "status": "CONFIRMED"
}

# 重新处理 | Reprocess
POST /api/invoices/{id}/process?use_ocr=true&use_llm=true

# 批量操作 | Batch Operations
POST /api/invoices/batch-update
POST /api/invoices/batch-delete
POST /api/invoices/batch-reprocess

# 统计数据 | Statistics
GET /api/invoices/statistics

# 单发票导出
GET /api/invoices/export/csv
GET /api/invoices/export/excel

# LLM配置 | LLM Configuration
GET  /api/settings/llm/status
POST /api/settings/llm/configure
POST /api/settings/llm/test
POST /api/settings/llm/test-config
GET  /api/settings/models

# 月度资料批次 | Monthly document batches
POST /api/batches
GET  /api/batches
GET  /api/batches/{id}
GET  /api/batches/{id}/documents
GET  /api/batches/{id}/export/excel
POST /api/batches/{id}/export/filtered
POST /api/batches/{id}/reprocess
```

创建批次使用 `multipart/form-data`：

| 字段 | 格式 |
|------|------|
| `employee_name` | 非空字符串，最长 100 字符 |
| `expense_month` | `YYYY-MM` |
| `files` | 重复文件字段 |
| `relative_paths` | 与 `files` 数量、顺序完全一致的重复字符串字段 |

目录既可直接以 `发票/...` 开头，也可带一个不透明根目录：

```text
马耿宾2025年3月报销发票/
├── 发票/电子发票.pdf
├── 补票/项目A/补充票据.jpg
└── 支付凭证/项目A/支付截图.png
```

只允许 PDF、JPG/JPEG、PNG、MP4/MOV/AVI/MKV；拒绝绝对路径、盘符、`.`/`..`、未知来源、空文件和文件名不匹配。服务端按 1 MiB 读取块计数并计算哈希，这不是客户端断点续传。三类来源目录不要求全部出现。

重处理请求体可为空，或传 `{"document_ids":[1,2]}`。只会重置人工复核/失败且仍可处理的文档；处理中、视频、汇总 PDF 和超像素长图不会重复领取。重新安排后批次回到 `UPLOADED`，随后进入 `PROCESSING`。

`POST /api/batches` 成功返回 `202` 和 `Location: /api/batches/{id}`；路径/文件业务校验失败返回 `400`，表单格式失败返回 `422`，批次不存在返回 `404`。列表统一返回 `{items,total,page,page_size}`。

`GET /api/batches/{id}/export/excel` 下载当前批次的全部文档记录，不受文档分页影响；不存在的批次返回 `404`。前端仅在批次进入终态（`COMPLETED` 或 `FAILED`）后启用导出按钮。

`POST /api/batches/{id}/export/filtered` 生成审核及分类汇总工作簿，共 7 张工作表。`发票补票_分类汇总` 和 `支付凭证_分类汇总` 按费用组连续排列 A:J 单据明细，并把 K:N 的申请人、费用类型、费用详细事项和合计金额纵向合并到对应明细行；发票与支付分别汇总，不做跨来源差额判断。分类汇总不推断支付主体；全部金额按人民币单币种口径汇总，不展示币种、不按币种拆组，金额缺失时合计留空并提示。分类表为保护合并结构不提供筛选，分类技术信息统一保存在 `分类审计明细`，所有结果均在导出时重新计算，不写入数据库。

费用分类规则 2.0 优先使用 `差旅费、交通费、快递费、物流费、项目成本、招待费、职工福利费`，并继续支持办公费及其他明确费用类型。历史名称“福利费”等统一显示为“职工福利费”；“合计”仅用于金额汇总，“合计”和“会计”都不会作为费用类型，辅助表出现这些标签时会忽略标签并重新分类。

完整 API 文档请访问 http://localhost:18080/docs

---

## 🗺️ 路线图 | Roadmap

- [x] **v0.1** - 基础OCR识别 + 发票管理
- [x] **v0.2** - 多LLM支持 + 双源验证
- [x] **v0.3** - UI全面改版 + OpenRouter模型支持
- [x] **v0.4** - 月度多资料识别第一阶段 ✨ **当前版本**
- [ ] **v0.5** - 月度资金表核对与报表增强
- [ ] **v0.6** - 发票真伪验证 (对接税务API)
- [ ] **v0.7** - 移动端适配
- [ ] **v1.0** - 企业级功能 (审批流程、ERP集成)

欢迎提 Issue 告诉我们你最需要的功能！

---

## 🛠️ 技术栈 | Tech Stack

<table>
<tr>
<td align="center" width="50%">

**Backend**

![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white)

- FastAPI 0.109 (Async REST API)
- SQLAlchemy 2.0 + asyncpg (Async ORM)
- PaddleOCR 2.7 (Chinese OCR)
- Multi-LLM SDK Integration

</td>
<td align="center" width="50%">

**Frontend**

![React](https://img.shields.io/badge/React-20232A?style=for-the-badge&logo=react&logoColor=61DAFB)
![TypeScript](https://img.shields.io/badge/TypeScript-007ACC?style=for-the-badge&logo=typescript&logoColor=white)
![Ant Design](https://img.shields.io/badge/Ant%20Design-0170FE?style=for-the-badge&logo=antdesign&logoColor=white)

- React 18.2 + TypeScript
- Vite 5.0 (Build Tool)
- Ant Design 5.13 (UI Components)
- CSS Modules + CSS Variables
- Space Grotesk + Inter (Typography)
- Axios (HTTP Client)

</td>
</tr>
</table>

---

## 📁 项目结构 | Project Structure

```
invoice_manager/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── models/            # SQLAlchemy 数据模型
│   │   │   ├── invoice.py     # 旧单发票账本
│   │   │   └── batch.py       # 批次、来源文档和状态枚举
│   │   ├── routers/           # API 路由
│   │   │   ├── invoices.py    # 发票 CRUD + 处理
│   │   │   ├── batches.py     # 月度批次 API
│   │   │   └── settings.py    # LLM 配置
│   │   ├── services/          # 业务逻辑
│   │   │   ├── ocr_service.py # PaddleOCR 识别
│   │   │   ├── llm_service.py # 多 LLM 调用
│   │   │   ├── invoice_service.py # 旧发票处理流程
│   │   │   ├── batch_service.py   # 上传、统计、协调与恢复
│   │   │   └── document_service.py # 多资料识别与映射
│   │   ├── schemas/           # Pydantic 数据验证
│   │   ├── config.py          # 配置管理
│   │   └── main.py            # 应用入口
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/                   # React 前端
│   ├── src/
│   │   ├── pages/             # 页面组件
│   │   │   ├── UploadPage.tsx # 上传页面
│   │   │   ├── BatchPage.tsx  # 月度资料工作台
│   │   │   ├── InvoiceListPage.tsx # 列表页面
│   │   │   └── InvoiceDetailPage.tsx # 详情页面
│   │   ├── components/        # 通用组件
│   │   │   ├── layout/        # 布局组件
│   │   │   │   ├── MainLayout.tsx # 主布局 (侧边栏+内容)
│   │   │   │   └── Sidebar.tsx    # 侧边导航栏
│   │   │   ├── dashboard/     # 仪表盘组件
│   │   │   │   ├── MetricCard.tsx # 统计卡片
│   │   │   │   └── ControlBar.tsx # 控制栏
│   │   │   └── StatusTag/     # 状态标签组件
│   │   ├── styles/            # 全局样式
│   │   │   ├── variables.css  # CSS 变量 (设计规范)
│   │   │   ├── global.css     # 全局样式
│   │   │   └── antd-overrides.css # Ant Design 覆盖
│   │   ├── services/          # API 调用
│   │   └── types/             # TypeScript 类型
│   ├── package.json
│   └── Dockerfile
│
├── docs/                       # 文档
│   ├── images/                # 截图
│   ├── invoice-manager.pen    # UI 设计稿 (Pencil)
│   └── UI_REDESIGN.md         # 设计规范文档
│
├── docker-compose.yml          # 容器编排
└── README.md
```

---

## 🤝 参与贡献 | Contributing

我们欢迎所有形式的贡献！无论是：

- 🐛 **报告 Bug** - 发现问题请提 Issue
- 💡 **功能建议** - 告诉我们你想要什么功能
- 📝 **完善文档** - 帮助改进文档
- 🔧 **提交代码** - Fork & PR

### 本地开发 | Local Development

```powershell
# 终端 1：先启动 PostgreSQL（项目根目录）
docker compose up -d db

# 终端 2：后端
Set-Location backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5435/invoice_db'
uvicorn app.main:app --host 0.0.0.0 --port 18080 --reload

# 终端 3：前端（从项目根目录进入）
Set-Location frontend
npm install
$env:VITE_API_URL='http://localhost:18080'
npm run dev
```

验证：

```powershell
# 后端虚拟环境已激活时，在项目根目录运行
python -m pytest -q backend/tests

# PostgreSQL 集成测试使用专用库，不要指向业务库
docker compose exec db createdb -U postgres invoice_test
$env:TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5435/invoice_test'
python -m pytest -q -m postgres_integration backend/tests

Set-Location frontend
npm run build
Invoke-RestMethod http://localhost:18080/api/health
```

### 代码规范 | Code Style

- Python: Black + isort + flake8
- TypeScript: ESLint + Prettier
- Commit: [Conventional Commits](https://www.conventionalcommits.org/)

---

## 💬 常见问题 | FAQ

<details>
<summary><b>Q: 不配置LLM可以使用吗？</b></summary>

A: 可以。旧单发票流会使用 OCR/规则并跳过 LLM；月度资料流只会自动通过规则分类、字段完整且置信度达标的文档，其余进入人工复核队列。配置 LLM 后，相关文本或图像会发送给所选提供商。
</details>

<details>
<summary><b>Q: 支持哪些发票类型？</b></summary>

A: 主要针对中国增值税发票（普通发票、专用发票）优化。其他格式的发票也可识别，但字段提取可能不完整。
</details>

<details>
<summary><b>Q: OCR 识别不准怎么办？</b></summary>

A: 1) 确保上传的图片清晰、方向正确；2) 配置 LLM 进行双源验证；3) 旧单发票可在详情页修正，月度批次第一阶段只展示原因并允许重新处理，不提供字段编辑/确认。
</details>

<details>
<summary><b>Q: 如何选择 LLM 提供商？</b></summary>

A:
- **国内用户**: 推荐通义千问或DeepSeek，速度快、价格低
- **准确率优先**: 推荐 GPT-4o 或 Claude 3.5 Sonnet
- **成本优先**: 推荐 GPT-4o-mini 或 Gemini 1.5 Flash
</details>

<details>
<summary><b>Q: 数据安全吗？</b></summary>

A: 系统完全本地部署，发票数据存储在你自己的服务器上。如果使用LLM，发票图片会发送到LLM提供商，请根据实际情况选择。
</details>

---

## 📜 License

[MIT License](LICENSE) - 可自由使用、修改、商用，但请保留版权声明。

---

## 🙏 致谢 | Acknowledgments

- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) - 强大的中文OCR引擎
- [FastAPI](https://fastapi.tiangolo.com/) - 现代Python Web框架
- [Ant Design](https://ant.design/) - 企业级UI组件库

---

<div align="center">

**如果这个项目对你有帮助，请给一个 ⭐ Star！**

**If this project helps you, please give it a ⭐ Star!**

<br/>

Made with ❤️ for the open source community

[Report Bug](../../issues) · [Request Feature](../../issues) · [Discussions](../../discussions)

</div>

---

<!-- SEO Keywords / 搜索关键词 -->
<!--
发票识别 发票OCR 中文发票 增值税发票 电子发票 发票管理系统 费用报销 财务自动化 票据识别
Invoice OCR Chinese Invoice VAT Invoice Invoice Recognition Document AI Receipt OCR
Expense Management Financial Automation PaddleOCR GPT-4 Claude LLM Invoice
开源发票 免费发票识别 Python发票 FastAPI React 发票数据提取 批量发票处理
Invoice Parser Invoice Scanner Invoice Extractor AI Invoice Processing
-->
