服务已经启动成功，三个容器均正常运行：

- 前端网站：http://localhost:15173
- 后端接口文档：http://localhost:18080/docs
- 数据库：`localhost:5435`

我已实际验证前端、后端和前端代理 API，均返回 HTTP `200`。此前打不开是因为 `invoice_frontend` 容器已经退出，目前已重新启动。

以后启动项目，在项目目录执行：

```powershell
docker compose up -d
```

查看运行状态：

```powershell
docker compose ps
```

查看日志：

```powershell
docker compose logs -f
```

如果只有前端退出，可以单独启动：

```powershell
docker compose up -d frontend
```

如果浏览器仍显示旧错误，访问 `http://127.0.0.1:15173`，并按 `Ctrl+F5` 强制刷新。端口映射定义在 [docker-compose.yml](F:/Dene_internship/0721finance_charge_reimbursement/finance_code/Finance_handle_tool/docker-compose.yml:34)。

# 重启后端
docker compose restart backend

# 重启前端，注意正确名称是 frontend，不是 fronted
docker compose restart frontend

# 重启 OCR 后台任务
docker compose restart ocr_worker

# 重启 PostgreSQL
docker compose restart db