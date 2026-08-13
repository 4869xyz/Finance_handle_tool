# 月度资料识别分流改造

> 状态：实施中  
> 决策：1A 仅本地规则/FieldExtractor；2B 本阶段不做 worker 并发

## 需求背景

月度资料识别当前对图片几乎总走 vision LLM，吞吐慢、成本高。改为按类型分流：标准发票/车票规则优先，支付/订单截图 OCR 文本 LLM 优先，仅难例升级 vision 或人工复核。

## [Conflict]

- 旧策略（ARCHITECTURE / services README / Prompt 4.1 说明）：LLM 主提取，合法结果不与规则逐字段核对，仅整单兜底。
- 新策略：结构化票据 **整单规则优先**，失败再整单升 LLM；截图 **文本 LLM 优先**，缺字段再 vision。不做字段级混拼。
- 文档漂移：ARCHITECTURE 写「全局并发 2」，`ocr_worker` 实际串行；本阶段不修并发。

## 受影响文件

- `backend/app/services/document_service.py`
- `backend/app/services/ocr_service.py`（仅复用 FieldExtractor，尽量不改）
- `backend/tests/test_document_processing.py`
- `ARCHITECTURE.md`
- `backend/app/services/README.md`
- `VersionLog.md`
- 本文件

## Checklist

- [ ] 落盘本 plan
- [ ] 结构化类型规则优先：FieldExtractor→ExtractionModel + 行程/车票规则
- [ ] `process_document_content` A/B/C 分流、文本 LLM、vision 门控、`_validation`
- [ ] 更新/新增 `test_document_processing` 用例
- [ ] 同步 ARCHITECTURE / README / VersionLog 并勾选完成

## 验收

- 标准发票关键字段齐全 → `RULE_PRIMARY`，不调提取 LLM
- 支付截图 OCR 充足 → 仅 `chat_completion`
- OCR 稀疏支付图 → vision
- 规则失败升级链写入 `escalation_path`
- `pytest backend/tests/test_document_processing.py` 通过
