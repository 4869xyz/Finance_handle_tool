# Qwen 3.7 Plus 模型接入计划

## 目标

将现有 Qwen Provider 的默认模型切换为 DashScope 原生多模态模型
`qwen3.7-plus`，并确保设置页提供的模型 ID 可直接用于现有调用链。

## 成功标准

- 默认配置、示例配置和当前本地配置均使用 `qwen3.7-plus`。
- 后端与前端模型列表将 `qwen3.7-plus` 作为首选视觉模型。
- Qwen 模型列表使用 DashScope 原生 ID，不混入 OpenRouter 的 `qwen/` 前缀。
- 自动化测试验证默认值、原生模型列表和视觉能力判定。

## 实施状态

- [x] 更新后端默认配置、设置接口默认值和本地配置示例。
- [x] 更新后端与前端 Qwen 模型回退列表。
- [x] 将 `qwen3.7-plus` 纳入 Qwen 视觉能力判定。
- [x] 运行后端定向测试和前端构建。

## 验证命令

```powershell
docker compose exec -T backend python -m pytest -q `
  tests/test_exclusion_service.py::test_qwen_chat_options_are_scoped_per_request `
  tests/test_qwen_model_configuration.py

Set-Location frontend
npm run build
```

运行态还需通过 `/api/health`、`/api/settings/llm/status` 和
`POST /api/settings/llm/test` 验证健康状态、实际模型名与供应商连接。
