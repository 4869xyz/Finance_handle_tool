# VersionLog

> 版本标识 = 自然日 `YYYY-MM-DD`。同一天内变更追加到同一版本块。

## 2026-07-25

- 发布 v0.4.0 第一阶段月度多资料识别：新增 `processing_batches` / `source_documents` 账本、`/api/batches` API 和 `/batches` 前端工作台。
- 实现三类来源目录的严格相对路径校验、文件/路径配对、服务端按 1 MiB 分块读取并计算 SHA-256、200 文件/150 MiB 单文件/256 MiB 单批限制和失败整批回滚。
- 视频仅保存元数据并人工复核，精确命名 `PDF合并.pdf` 跳过，超 5000 万像素图片在解码/OCR/LLM 前进入人工复核。
- 扩展通用 PDF 全页文本层与多资料分类/提取，采用严格 Pydantic JSON Schema、类型关键字段矩阵和多笔交易无汇总保护。
- 增加全局并发 2 的后台领取、批次统计锁定重算、启动中断恢复和受限重处理；旧 `/api/invoices/*` 流程保持独立。
- 新增真实 `example_file` 登记测试与路径、原子性、短路、映射、状态、恢复、领取测试；前端生产构建通过。
- 清理 `backend/.env.example` 中的真实 Qwen 密钥痕迹，改用占位符并补充批次配置示例。历史中出现过的密钥仍需在供应商控制台撤销/轮换。

## 2026-07-24

- 按 Cursor 协作规范落地项目管理文档：新增 `ARCHITECTURE.md`、`.cursorrules`、`.cursorignore`、`VersionLog.md`、`docs/Cursor项目规范使用流程.md`。
- 为 `backend/`、`backend/app/{models,routers,services}`、`frontend/`、`frontend/src/`、`docs/` 补充目录级 `README.md`。
- 建立 `docs/plans/` 作为功能 plan 存放目录。
