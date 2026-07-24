# frontend/

前端应用根目录（React + Vite + Ant Design）。

## 成员

| 路径 | 职责 | 依赖 |
|------|------|------|
| `Dockerfile` | 前端开发镜像 | `package.json` |
| `package.json` | npm 脚本与依赖 | — |
| `vite.config.ts` | 端口 15173、路径别名 `@`、`/api` 代理到 backend | 环境变量 `VITE_API_URL` |
| `tsconfig.json` / `tsconfig.node.json` | TypeScript 配置 | — |
| `index.html` | HTML 入口 | `src/main.tsx` |
| `src/` | 源码 | 见 `src/README.md` |

## 访问

Docker 启动后：http://localhost:15173
