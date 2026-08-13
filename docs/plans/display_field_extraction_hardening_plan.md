# 展示字段抽取强化

> 状态：已完成
> 决策：吸收分类型窄抽取；避开 ExpenseRecord/extractors 大重构

## Checklist

- [x] 落盘本 plan
- [x] 高德行程规则 + 表头黑名单 + RULE_PRIMARY 起终点门控
- [x] 发票 item_name 清洗 + Prompt 4.2
- [x] 支付 order_number / transaction_number 分离
- [x] 测试（40 passed）
- [x] ARCHITECTURE / README / VersionLog 同步

## 验收

- 行程：起终点/时间/金额独立列；对方非表头
- 发票：项目名称干净 + 发票号 + 金额
- 支付：订单号与交易单号分离