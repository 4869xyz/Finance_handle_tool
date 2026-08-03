# 月度资料 Prompt 4.0 与交易级跨文件核对实施计划

## 目标

将月度识别升级为单文件交易级提取与批次级跨文件核对两阶段流程。单文件 Prompt 拆分订单、行程和转账并保留金额证据；Python 使用 Decimal 验算；批次核对 Prompt 判断主辅凭证与重复关系，去重结果实际影响月度分类合计。

## 已确认边界

- 同一图片多笔交易在下游按一笔一行展示和分类。
- 跨文件关系写入现有 `source_documents.extracted_fields_json`，不新增数据库表或字段。
- 主凭证由模型根据材料证据选择，不设置固定票据优先级；Python 校验引用、置信度和金额一致性。
- `possible_duplicate` 与 `unknown` 所在分类组不输出部分合计。
- 基础字段继续采用 LLM 主识别、整单失败才 OCR/Python 兜底。
- 工作簿保持7张表，基础识别结果保持文件级，其余审核与分类视图采用记录级。

## Checklist

- [x] 升级字段提取 Prompt 4.0 与严格交易记录 Schema
- [x] 实现订单、行程、转账和文件合计的 Decimal 验算
- [x] 实现交易记录视图及历史 JSON 兼容
- [x] 实现跨文件候选生成、核对 Prompt 1.0 与主辅凭证裁决
- [x] 自动保存 `_reconciliation` 并支持批次完成、重处理和手动重试
- [x] 将筛选、分类和7张表导出调整为记录级与去重金额
- [x] 增加后端 API 和前端“重新核对”操作
- [x] 更新架构、服务说明、计划目录和版本日志
- [x] 运行专项、完整后端测试与前端构建
- [x] 重启 `backend`、`ocr_worker` 并确认健康

## 验证命令

```powershell
docker compose exec -T backend python -m pytest tests/test_document_processing.py tests/test_document_reconciliation.py -q
docker compose exec -T backend python -m pytest tests/test_exclusion_service.py tests/test_expense_classification_service.py tests/test_batch_api.py -q
docker compose exec -T backend python -m pytest -q
docker compose exec -T frontend npm run build
docker compose restart backend ocr_worker
docker compose ps backend ocr_worker
```

## 状态

completed
