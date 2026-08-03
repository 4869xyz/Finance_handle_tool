# 文件名摘要最后兜底实施计划

## 目标

当月度资料无法从正文、图片、OCR 或 LLM 获得有效摘要时，使用去掉扩展名后的文件名作为 `description`。文件名不得补充日期、金额、对方、号码、单据类型或其他结构化字段。

## Checklist

- [x] 将文件名传入月度字段处理函数
- [x] 仅在摘要为空时采用文件名并记录 `FILENAME_FALLBACK`
- [x] 为视频、跳过文件和超像素图片保存文件名摘要
- [x] 增加字段边界和特殊资料测试
- [x] 更新 README、架构说明、服务说明和版本日志
- [x] 运行完整后端测试并重启服务

## 验证命令

```powershell
docker compose exec -T backend python -m pytest tests/test_document_processing.py tests/test_batch_service.py -q
docker compose exec -T backend python -m pytest -q
docker compose restart backend ocr_worker
docker compose ps backend ocr_worker
```

## 状态

completed
