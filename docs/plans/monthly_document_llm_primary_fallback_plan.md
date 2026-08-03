# 月度资料 LLM 主识别与整单规则兜底实施计划

## 目标

月度字段提取正常情况下完全采用 LLM 结果，不再使用 OCR/Python 逐字段补空或核对。只有模型不可用、调用或响应无效，或者日期、对方、金额和对应号码全部为空时，才整单切换到 OCR/Python 规则结果。

## 已确认边界

- OCR 仍先执行：图片把原图与 OCR 文字一起交给视觉模型，PDF 使用文字层或 OCR 全文。
- 合法 LLM 响应中的个别空字段保持为空，由关键字段完整性规则进入待复核。
- 低置信度 LLM 结果不切换规则分支，只按现有阈值进入待复核。
- Prompt 3.0、受控金额计算及其确定性算术校验保持不变。
- 不新增数据库或 API 字段；新流程证据继续写入 `extracted_fields_json._validation`。
- 历史双源校验结果不重写，历史 `OCR_LLM_FIELD_CONFLICT` 显示映射继续兼容。

## Checklist

- [x] 将 OCR/Python 字段规则改为仅在整单兜底时惰性执行
- [x] 删除 LLM 与规则的逐字段比较、补空及新冲突状态生成
- [x] 增加全部关键字段为空时的整单规则兜底
- [x] 简化 `_validation` 为最终来源、兜底状态、兜底原因及金额计算证据
- [x] 增加正常 LLM、部分空字段、全空、低置信度、无效响应和调用异常测试
- [x] 更新计划、架构、服务说明和版本日志
- [x] 运行完整后端测试和前端构建
- [x] 重启 `backend`、`ocr_worker` 并确认健康

## 验证命令

```powershell
docker compose exec -T backend python -m pytest tests/test_document_processing.py -q
docker compose exec -T backend python -m pytest -q
docker compose exec -T frontend npm run build
docker compose restart backend ocr_worker
docker compose ps backend ocr_worker
```

## 状态

done

## 验收结果

- 字段提取专项：27 passed。
- 完整后端回归：89 passed，4 skipped。
- 前端 TypeScript 与 Vite 生产构建通过，仅保留既有大 chunk 提示。
- `backend` healthy，`ocr_worker` 正常运行。
