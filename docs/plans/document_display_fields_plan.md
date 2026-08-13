# 月度识别展示字段增强（1A + 2B）

## 状态

- 状态：已完成
- 决策：Excel 一文件一行多值 `; ` 拼接；`source_documents` 新增可空列并与 JSON 双写；Prompt 4.1

## 需求背景

月度识别需稳定提取并展示：发票项目名称、行程起终点与时间、支付订单号；列表与 Excel「识别结果」可见。

## Checklist

- [x] 创建本计划文档
- [x] `SourceDocument` 新增 5 列 + 启动 `ADD COLUMN IF NOT EXISTS`
- [x] `document_prompts` 升至 4.1
- [x] ExtractionModel / 映射 / records 合成写入新列并修支付 `order_number`
- [x] schema、Excel、Batch 列表与前端 types
- [x] 单测 + ARCHITECTURE / README / VersionLog

## 受影响文件

- `backend/app/models/batch.py`
- `backend/app/database.py` / `backend/app/main.py` / `backend/app/ocr_worker.py`
- `backend/app/services/document_prompts.py`
- `backend/app/services/document_service.py`
- `backend/app/services/batch_service.py`
- `backend/app/schemas/batch.py`
- `backend/app/routers/batches.py`
- `frontend/src/types/batch.ts`
- `frontend/src/pages/BatchPage.tsx`
- `backend/tests/test_document_processing.py`
- `backend/tests/test_batch_api.py`
- `ARCHITECTURE.md` / `VersionLog.md` / `backend/app/services/README.md`
