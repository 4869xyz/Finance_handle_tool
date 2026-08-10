# 分类汇总识别金额与 Excel 异常样式实施计划

## 目标

分类汇总直接累加交易级识别金额，不再由跨文件核对结果改变合计；同时统一审核工作簿的异常底色和表格边框。

## 已确认边界

- 主凭证、辅助凭证和待确认重复记录均按 `amount` 原值参与分类汇总，核对结果继续保留作审计。
- 同组部分金额缺失时汇总已有金额并标记风险；全部缺失时合计为空。
- “识别结果”中所有非 `RECOGNIZED` 文件级记录整行使用浅红底。
- 单表导出和 7 表审核导出使用相同异常规则；7 张审核工作表的实际使用区域均使用细灰边框。
- 不修改 API、数据库、工作表名称、列顺序或现有核对接口。

## Checklist

- [x] 分类汇总改为读取交易级识别金额并支持部分金额合计
- [x] 两类“识别结果”导出增加非正常行浅红底
- [x] 7 张审核工作表增加统一细灰边框
- [x] 更新导出提示文案和专项测试
- [x] 同步架构、README、目录说明和版本日志
- [x] 运行完整后端测试、前端构建与代表性工作簿检查

## 验证命令

```powershell
docker compose exec -T backend python -m pytest tests/test_expense_classification_service.py tests/test_batch_api.py -q
docker compose exec -T backend python -m pytest -q
docker compose exec -T frontend npm run build
```

## 状态

completed
