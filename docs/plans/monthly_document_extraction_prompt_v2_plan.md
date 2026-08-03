# 月度资料字段提取 Prompt 2.0 实施计划

## 目标

将月度批次字段提取改为“公共约束 + 单据类型专用规则”，继续使用当前活动 LLM，并以保守识别方式约束金额、日期、对方、号码和事项。Prompt 只输出既有严格 JSON Schema，不增加数据库、API 或第二次模型调用。

## 已确认边界

- Prompt 在代码中版本化，版本号写入运行日志。
- 发票、支付凭证、订单、行程及车票、收据和其他资料分别使用专用取值规则。
- 不确定字段返回 `null`；禁止从文件名、目录名、批次月份、其他文件或常识补值。
- 多笔交易没有明确汇总金额时，公开汇总金额为空，逐笔候选保留在 `transactions`。
- 保留 LLM 失败后回退 OCR/规则提取以及现有待复核判定。
- 不修改单张发票旧流程、费用分类 Prompt 或剔除规则。

## Checklist

- [x] 增加 Prompt 2.0 版本、公共约束和分类型规则
- [x] 接入月度资料字段提取调用并记录 Prompt 版本日志
- [x] 增加 Prompt 覆盖、Schema、类型映射、回退和多笔交易测试
- [x] 更新服务说明、架构说明和版本日志
- [x] 运行文档识别、批次导出和完整后端回归测试
- [x] 重启 `backend` 与 `ocr_worker` 并确认服务健康

## 验证命令

```powershell
docker compose exec -T backend python -m pytest tests/test_document_processing.py -q
docker compose exec -T backend python -m pytest tests/test_batch_service.py tests/test_batch_api.py -q
docker compose exec -T backend python -m pytest -q
docker compose restart backend ocr_worker
docker compose ps backend ocr_worker
```

## 状态

done

## 验收结果

- 字段提取 Prompt 专项：18 passed。
- 批次处理和月度导出回归：12 passed，3 skipped。
- 完整后端回归：76 passed，4 skipped。
- `backend` 重启后 healthy，数据库健康；`ocr_worker` 正常运行并轮询待识别队列。
