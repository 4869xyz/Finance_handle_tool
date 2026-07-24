# Docker 学习复盘（本项目）

> 基于本仓库问答整理，方便后续复习。  
> 对应项目：财务报销 / 发票处理工具（`Finance_handle_tool`）

---

## 1. Docker 是什么、有什么用

**一句话**：Docker 把「软件 + 运行环境」打包成可随时启动的盒子，这个盒子叫**容器（container）**。

### 解决什么问题

本地开发常见痛点：

- 你装了 Python 3.11，同事是 3.12，跑不起来
- 要自己装 PostgreSQL、配端口、配密码
- 「我电脑上能跑，你那边不行」

Docker 的思路：环境写进配置文件，大家一键拉起**同一套**服务。

### 核心概念（记住 3 个）

| 概念 | 比喻 | 作用 |
|------|------|------|
| **镜像 Image** | 安装包 / 模板 | 预先做好的环境快照 |
| **容器 Container** | 按模板启动的运行实例 | 真正在跑的进程 |
| **Compose** | 一键启动整套服务的脚本 | 用 `docker-compose.yml` 同时管多个容器 |

---

## 2. Docker 算不算「环境管理工具」

**可以这么理解，但范围更大。**

| 工具 | 管什么 |
|------|--------|
| `.venv` / conda | 主要管 **Python 包** |
| nvm | 主要管 **Node 版本** |
| Docker | 管 **系统 + 运行时 + 依赖 + 服务怎么启动** |

- 说它「也能做环境管理」——对  
- 把它等同于 `.venv`——偏小了  

`.venv` 解决：「这个项目用哪套 Python 包」  
Docker 解决：「整套服务（系统库、数据库、后端、前端）怎么一致地跑起来」

---

## 3. 在本项目里 Docker 做什么

本仓库的 `docker-compose.yml` 一次拉起 **3 个服务**：

1. **db** — PostgreSQL 数据库（主机端口 `5435` → 容器 `5432`）
2. **backend** — FastAPI 后端（端口 `18080`）
3. **frontend** — Vite 前端（端口 `15173`）

关系大致是：

```text
浏览器 → frontend(:15173) → backend(:18080) → db(:5435)
```

常用启动方式：

```bash
docker compose up
```

### 和「直接本地跑」的对比

| | 本地直接跑 | 用 Docker |
|--|-----------|-----------|
| 数据库 | 自己装、自己配 | 容器里自动有 |
| 依赖 | pip / npm 自己装 | 镜像里已打包 |
| 换电脑 / 换同事 | 容易踩环境坑 | 配置更一致 |
| 资源占用 | 较轻 | 稍重（多一层） |

**结论**：Docker 不是业务功能本身，而是让团队少折腾环境、稳定跑起这套工具的基础设施。

---

## 4. 用 Docker 还要不要本机 `.venv`？

**如果主路径是 Docker 跑项目：一般不需要依赖本机 `.venv`。**

| | Docker 方式 | 本地 `.venv` 方式 |
|--|------------|------------------|
| Python 包 | 在 **backend 容器**里 | 在本机 `.venv` |
| Node 依赖 | 在 **frontend 容器**里 | 在本机 `node_modules` |
| 数据库 | **db 容器**里的 Postgres | 本机自己装 Postgres |
| 启动方式 | `docker compose up` | 本机分别启动前后端 |

补充两点：

1. **`.env` 仍是本地文件**  
   例如 `backend/.env`，它不是虚拟环境，只是配置（密钥、连接串等）。Docker 会读它并传入容器。

2. **两种方式可以并存，但日常通常二选一当主路径**  
   - 用 Docker：环境在容器里  
   - 不用 Docker、本机直接跑：才需要 `.venv` + 本机 Postgres  

开发时本仓库还挂载了代码目录（如 `./backend:/app`），所以本地改代码，容器里通常能立刻看到（开发热重载）。

---

## 5. 项目里哪些文件跟 Docker 有关

### 5.1 专门为 Docker 写的（核心 3 个）

| 文件 | 作用 |
|------|------|
| `docker-compose.yml` | 总编排：db / backend / frontend 怎么启动、端口、挂载、依赖 |
| `backend/Dockerfile` | 后端镜像怎么构建（Python、系统库、装 `requirements.txt`） |
| `frontend/Dockerfile` | 前端镜像怎么构建（Node、装 npm 依赖） |

### 5.2 Docker 会用到、但本身不是 Docker 专用文件

| 文件 | 关系 |
|------|------|
| `backend/.env` | `docker-compose.yml` 的 `env_file` 会读它 |
| `backend/requirements.txt` | backend Dockerfile 里 `pip install` 用 |
| `frontend/package.json` | frontend Dockerfile 里 `npm install` 用 |

### 5.3 日常通常动什么

- **第一次跑**：装 Docker Desktop，准备好 `backend/.env`，再 `docker compose up`
- **一般开发**：改业务代码即可，不必天天改 Dockerfile
- **才需要改 Docker 文件**：加服务、改端口、改启动命令、加系统依赖、换基础镜像等

---

## 6. 开发体验上，Docker 带来什么变化

### 没有 Docker 时（本机手动）

大致要：

1. 本机准备 Postgres  
2. 后端：装依赖 → 用类似 `uvicorn` 启动  
3. 前端：装依赖 → 用类似 `npm run dev` 启动  

（不一定是随便跑 `app.py` / `main.py`；本项目更接近上面这套。）

### 有 Docker 时

- 启动命令写进了 `docker-compose.yml` / `Dockerfile`
- 开发者一条 `docker compose up`，相当于帮你把环境 + 多服务启动都做了
- 之后主要专注改代码，跑项目更简便

**注意**：业务代码还是前后端那套；变简单的是「装环境 + 起服务」这件事。

---

## 7. 总复习（一页纸）

1. Docker = 把运行环境和服务一起打包、隔离、复现的工具  
2. 本项目用它做：环境一致 + 一键启动 db / 后端 / 前端  
3. 用 Docker 为主时，一般不靠本机 `.venv`；`.env` 仍要本地配置  
4. 仓库里真正「为 Docker 写的」主要是：`docker-compose.yml` + 两个 `Dockerfile`  
5. 日常开发：改代码；Docker 文件只在改运行/部署方式时才动  

---

## 8. 建议复习命令（可选）

```bash
# 查看编排配置
# 打开 docker-compose.yml

# 启动全部服务（前台看日志）
docker compose up

# 后台启动
docker compose up -d

# 查看容器状态
docker compose ps

# 停止并移除容器（数据卷是否保留看你是否加 -v）
docker compose down
```

> 具体端口、容器名以当前仓库 `docker-compose.yml` 为准。

---

## 附录：本次问答脉络

1. Docker 是做什么的？→ 打包环境 + 容器化运行  
2. 环境是不是都在 Docker 里配？→ 用 Docker 时主要在容器里，不必依赖 `.venv`  
3. Docker 算不算环境管理工具？→ 算，但比 `.venv` 管得更广  
4. 项目里哪些文件跟 Docker 有关？→ compose + 两个 Dockerfile（及相关配置引用）  
5. 是不是启动更简单、开发主要改代码？→ 是，简化的是环境与启动，不是取消业务代码  
