# 报销总表缺件核验（提交包）

> 状态：实施中
> 验收包：`f:\Dene_internship\0721finance_charge_reimbursement\1-马耿宾\马耿宾-报销`

## 输入 / 输出

- **输入**：一次上传 `姓名-报销` 大文件夹（`*报销总表.xlsx` + 各月 `发票/补票/支付凭证`）。
- **输出**：总表副本，右侧新增「系统审核」列。通过只写 `通过`。提交人备注列不改。
- 现有单月上传与 7 张识别导出保持不变。

## 已锁定规则

- 月度文件夹两种命名：`马耿宾2025年4月报销发票`、`姓名-2026年1月-报销`。
- 「时间」只用来落到年-月；空票号/空专票普票不核票号票种。
- 一行默认一张票；例外 A：事项为 `打车` 或 `本月打车` 且金额等于当月未占用打车类发票合计（到分）。
- 支付截图可一对多，不独占。
- 打车加总不容差。未匹配到发票不写分类结论。

## Checklist

- [x] 将本计划落盘（本文件）
- [x] `reimbursement_packages` 模型/schema；`processing_batches.package_id` + ensure 列
- [x] 抽出 `register_batch_from_payloads`
- [x] 路径解析与 `POST /api/packages`
- [x] 总表解析与写回「系统审核」
- [x] 1:1 匹配、打车加总、支付一对多、分类对比
- [x] 子批次终态后自动审核；`POST audit` 与 `GET export/claims`
- [x] BatchPage 提交包上传、进度、下载
- [x] 合成夹具测试；ARCHITECTURE / README / VersionLog

## 受影响文件

- `backend/app/models/batch.py`、`backend/app/models/reimbursement_package.py`（新）
- `backend/app/database.py`、`backend/app/config.py`、`backend/app/main.py`、`backend/app/ocr_worker.py`
- `backend/app/schemas/batch.py`、`backend/app/schemas/package.py`（新）
- `backend/app/services/batch_service.py`
- `backend/app/services/package_ingest_service.py`（新）
- `backend/app/services/claim_workbook_service.py`（新）
- `backend/app/services/claim_completeness_service.py`（新）
- `backend/app/routers/packages.py`（新）
- `frontend/src/pages/BatchPage.tsx`、`frontend/src/services/api.ts`、`frontend/src/types/batch.ts`
- `backend/tests/test_package_*.py`（新）
- `ARCHITECTURE.md`、`VersionLog.md`、相关 README

