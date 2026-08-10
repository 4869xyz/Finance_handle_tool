# 月度审核分类导出速度优化实施计划

## 目标

将审核及分类结果按交易记录缓存到既有 JSON。普通导出优先读取缓存，不因缺少跨文件核对而阻塞；首次缓存未命中时以有限并发调用统一审核分类模型，并保留可审计的耗时日志。

## 已确认边界

- 排除记录保持当前展示与分类汇总口径，不从分类表移除。
- 普通导出不自动触发缺失的跨文件 LLM 核对；审计表显示未核对状态，现有手动核对接口保留。
- 缓存按 `source_document_id:record_id` 写入 `extracted_fields_json._audit_classification.records`，不新增数据库列或迁移。
- 缓存有效性要求源记录哈希、Prompt/策略版本、公司档案版本、辅助表版本和模型配置一致。
- 金额继续由 Python 从交易级 `amount` 读取并用 `Decimal` 汇总，模型不得计算或修改金额。
- 统一审核分类服务只处理缓存未命中的已识别记录；非已识别记录保持规则回退和待复核状态。

## Checklist

- [x] 确认现有调用链、模型、JSON 扩展字段与记录唯一键。
- [x] 实现统一审核分类服务、缓存序列化与失效哈希。
- [x] 实现有限并发、重试、拆分和安全耗时日志。
- [x] 改造普通导出读取缓存并跳过自动核对。
- [x] 增加缓存命中、局部失效、缺失核对、并发和回归测试。
- [x] 更新架构、说明和版本日志；运行完整验证。

## 验证结果

- `docker compose exec -T backend python -m pytest tests/test_audit_classification_service.py tests/test_batch_api.py tests/test_expense_classification_service.py -q`：28 passed
- `docker compose exec -T backend python -m pytest -q`：104 passed，4 skipped
- `docker compose exec -T frontend npm run build`：通过

## 状态

completed
