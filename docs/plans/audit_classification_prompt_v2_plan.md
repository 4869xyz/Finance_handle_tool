# 审核与费用分类 Prompt 2.0 优化计划

## 目标

将统一审核分类提示词升级为结构化 Prompt 2.0，补充证据优先级、分类边界、置信度标尺、少量示例和输出检查；拆分审核与分类复核信号，使模型的不确定性只影响对应阶段。

## 已确认边界

- 保留差旅费、交通费、快递费、物流费、项目成本、招待费、职工福利费和办公费 8 个标准类型，并允许稳定、可汇总的其他财务类型。
- 自动分类阈值维持 `0.75`；提示词倾向给出最有证据的分类，但不得用泛化标签、商户名或商品名创造费用类型。
- Python 明确规则及辅助表强匹配继续优先，模型复核标记只影响模型兜底结果。
- 不修改 API、数据库字段、Excel 布局、金额口径或并发设置。

## Checklist

- [x] 升级统一审核分类 Prompt 与输出字段。
- [x] 接入独立审核/分类复核标记和分类证据来源。
- [x] 增加边界、输出校验、缓存失效和复核隔离测试。
- [x] 同步架构、README 与版本日志。
- [x] 运行定向、全量和前端构建验证。

## 验证命令

```powershell
docker compose exec -T backend python -m pytest tests/test_audit_classification_service.py tests/test_expense_classification_service.py tests/test_batch_api.py -q
docker compose exec -T backend python -m pytest -q
docker compose exec -T frontend npm run build
```

## 状态

completed

## 验证结果

- 定向后端测试：34 passed。
- 后端全量测试：110 passed，4 skipped。
- 前端生产构建：通过。
