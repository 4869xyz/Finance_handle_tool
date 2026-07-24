# frontend/src/

前端源码。

## 成员

| 路径 | 职责 | 依赖 |
|------|------|------|
| `main.tsx` | React 挂载入口 | `App.tsx`、样式 |
| `App.tsx` | 路由：上传 / 列表 / 详情 | pages、layout |
| `index.css` | 样式入口 | styles/* |
| `pages/` | 页面：`UploadPage`、`InvoiceListPage`、`InvoiceDetailPage` | components、api、hooks |
| `components/` | 可复用 UI：布局、MetricCard、StatusTag、LLMConfigModal 等 | antd、types |
| `services/api.ts` | **唯一**后端 API 客户端 | `/api/*` |
| `hooks/` | `useColumnSettings`、`useInvoiceStatistics` | api、types |
| `types/invoice.ts` | 前端发票相关类型 | 与后端 schema 对齐 |
| `styles/` | 设计变量、全局样式、antd 覆盖 | CSS variables |

## 数据流

页面事件 → `services/api.ts` → Vite 代理 `/api` → backend → 回写 UI 状态
