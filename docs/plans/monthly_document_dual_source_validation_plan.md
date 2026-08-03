# 月度资料 LLM 主识别与 OCR/Python 二次校验实施计划

> 已于 2026-07-31 被 [月度资料 LLM 主识别与整单规则兜底实施计划](monthly_document_llm_primary_fallback_plan.md) 替代。本文件仅保留历史实施记录，不再作为当前识别流程依据。

## 目标

月度字段提取采用 LLM 主识别、OCR/Python 独立候选校验。图片把原图和 OCR 文字一起交给视觉 LLM，PDF 保持全文文字输入；LLM 空值由高可信规则补充，冲突保留 LLM 但进入人工复核。

## 已确认边界

- 文档类型仍采用高可信规则优先、歧义时 LLM；本功能只调整字段提取。
- Prompt 升级为 3.0，允许有明确组成和加减关系的受控金额计算，所有计算金额强制复核。
- 不新增数据库表或字段；双源候选、来源、冲突和计算依据保存在 `extracted_fields_json._validation`。
- 不进行模糊匹配，不修改单张发票旧流程、费用分类规则或剔除规则。
- 已有结果不自动重算，由用户发起重新处理。

## Checklist

- [x] 图片字段提取始终向视觉 LLM 发送原图和 OCR 文字，PDF 保持文本调用
- [x] OCR/Python 每次独立提取候选并实现 LLM 主值、规则补空和字段冲突裁决
- [x] 增加 Prompt 3.0 金额来源、计算依据和组成项，并以 Python 校验计算结果
- [x] 在现有 JSON、状态说明和分类风险说明中记录双源校验证据
- [x] 增加视觉输入、补空、一致、冲突、标准化、计算及回退测试
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

- 字段提取双源专项：25 passed。
- 批次、月度导出和分类风险回归：26 passed，3 skipped；风险说明追加专项：15 passed。
- 完整后端回归：84 passed，4 skipped。
- 前端 TypeScript 与 Vite 生产构建通过，仅保留既有大 chunk 提示。
- `backend` healthy、数据库 healthy；`ocr_worker` 正常运行并轮询待识别队列。
