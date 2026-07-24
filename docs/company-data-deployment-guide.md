# AskData 公司数据部署文档

本文档说明 AskData 在公司数据场景下的部署、配置、验证、运维和回滚流程。文档面向正式部署；答辩或演示只作为一个独立小节处理。

AskData 的部署流程按五个阶段组织：部署规划、环境配置、测试验证、正式部署、部署后监控与维护。这里的重点不是“把服务跑起来”，而是确保大模型、后端、前端、公司只读数据库、权限和状态持久化都能稳定协同。

## 1. 部署目标

AskData 部署后应提供以下能力：

- 用户选择已授权数据源。
- 用户用中文或英文自然语言提问。
- 系统检索数据库 Schema 和业务术语。
- 大模型通过 tool calling 调用 SQL 执行工具。
- 后端校验 SQL 安全性和权限。
- 后端只读执行 SQL。
- 前端展示回答、SQL、表格、图表、分析和 Trace。
- 管理员维护公司数据源、权限和业务术语。

公司数据接入的关键边界：

- AskData 不备份公司数据库。
- AskData 不保存公司业务数据行。
- AskData 本地只保存数据源配置、Schema Catalog、权限、术语和会话状态。
- 公司数据库必须使用只读账号。
- 大模型必须支持 tool calling。

## 2. 系统架构

```text
Browser
  |
  | X-User-ID / X-Admin-Token / X-Request-ID
  v
React Frontend
  |
  | /api/*
  v
FastAPI Backend
  |
  |-- DataSourceStore
  |     |-- datasources.sqlite
  |     |-- schema_catalogs
  |
  |-- PermissionStore
  |     |-- permissions.sqlite
  |
  |-- KnowledgeStore
  |     |-- knowledge.sqlite
  |
  |-- SessionManager
  |     |-- sessions.sqlite
  |     |-- session_history
  |
  |-- LangGraph checkpoint store
  |     |-- langgraph_checkpoints.sqlite
  |
  |-- SemanticRetriever
  |     |-- BIRD processed schema
  |     |-- synced company Schema Catalog
  |
  |-- AgentGraph
        |-- ReActSqlAgent
        |     |-- LLM.Chat(tools=[run_query])
        |     |-- query_runner.Execute(sql, database_path)
        |
        |-- SQLExecutor
              |-- SQLValidator
              |-- SQLAlchemy engine
              |-- row / byte / timeout limits
              |-- SQLite / MySQL / PostgreSQL execution
```

公司数据库查询路径：

1. 管理员注册数据源，例如 `company_mysql`。
2. 数据源路径使用 `env:COMPANY_MYSQL_URL`，真实连接串放在环境变量或 `.env`。
3. 管理员测试连接。
4. 管理员同步 Schema。
5. 后端保存 Schema Catalog 和结构指纹。
6. `/api/metadata/databases` 返回该公司数据源。
7. 用户选择公司数据源并提问。
8. Retriever 用已同步 Catalog 构造 Schema Prompt。
9. ReAct Agent 调用大模型。
10. 大模型通过 `tool_calls` 调用 `run_query(sql)`。
11. 后端验证 SQL、权限和行过滤。
12. 后端以只读方式执行 SQL。
13. 前端展示结果。

## 3. 部署组件

| 组件 | 说明 | 必需 |
| --- | --- | --- |
| Backend | FastAPI + Agent + SQL 执行 | 是 |
| Frontend | React + Vite + ECharts | 是 |
| LLM 服务 | OpenAI-compatible Chat Completions | 是 |
| 公司数据库 | MySQL 或 PostgreSQL，只读账号 | 是 |
| BIRD 数据 | 本地 SQLite/processed schema，用于内置样例 | 可选但建议保留 |
| 状态目录 | `ASKDATA_STATE_DIR`，默认 `.checkpoints` | 是 |
| 可信网关/SSO | 生产身份注入和 TLS 终止 | 生产必需 |

## 4. 容量规划

AskData 不保存公司业务数据行，因此容量压力主要来自代码依赖、前端构建产物、本地测试数据和控制面状态。

| 项目 | 估算 | 说明 |
| --- | --- | --- |
| 源码 | 小于 200 MB | 不含虚拟环境、node_modules、数据集 |
| Python 虚拟环境 | 500 MB 到 2 GB | 取决于 Python 版本和依赖缓存 |
| 前端依赖与构建 | 500 MB 到 2 GB | `node_modules` 最大；`dist` 较小 |
| BIRD 测试数据 | 取决于放入的 SQLite 数据库 | 仅用于内置样例和测试，不是公司数据必需项 |
| `ASKDATA_STATE_DIR` | 通常小于 1 GB | 保存 sessions、checkpoints、datasources、schema catalogs、permissions、knowledge |
| Docker image/cache | 2 GB 到 8 GB | 取决于本机 Docker 缓存和重建次数 |
| 公司数据库结果 | 不持久化 | 请求响应返回后由前端展示；不作为本地数据集保存 |

生产建议：

- 将 `ASKDATA_STATE_DIR` 放在持久化磁盘或持久卷。
- 对 `ASKDATA_STATE_DIR` 做周期备份。
- 不要把公司数据库 dump 放进项目仓库。
- 如果保留 BIRD 数据，只挂载需要的数据库子集。
- Docker 部署至少预留 10 GB 可用空间；如果本机已有大量镜像，应额外预留。

## 5. 大模型要求

AskData acceptance 分支的核心链路使用 ReAct tool-calling Agent。模型必须支持：

- OpenAI-compatible Chat Completions API。
- `tools` 请求参数。
- `tool_choice="auto"`。
- 响应中的 `message.tool_calls`。
- 工具参数 JSON 生成。

不支持 tool calling 的模型不适合部署核心问数链路。常见失败表现：

- 模型只输出 SQL 文本，但后端没有执行。
- Trace 中没有 `run_query` 工具调用。
- 查询返回通用失败信息。
- SQL 修复循环无法进行。

推荐配置：

```env
LLM_API_BASE=https://api.deepseek.com
LLM_API_KEY=your_key
LLM_MODEL_NAME=deepseek-v4-pro
LLM_THINKING_ENABLED=true
LLM_REASONING_EFFORT=high
```

如果模型服务不支持 `thinking` 扩展参数，设置：

```env
LLM_THINKING_ENABLED=false
```

## 6. 数据库要求

### 6.1 MySQL

建议使用 MySQL 作为公司数据首选部署路径，因为当前依赖中已包含 `pymysql`。

连接串格式：

```env
COMPANY_MYSQL_URL=mysql+pymysql://readonly_user:password@host:13306/database?charset=utf8mb4
```

数据库账号要求：

- 允许 `SELECT`。
- 允许读取元数据。
- 禁止写入和 DDL。
- 建议数据库侧设置查询超时。

### 6.2 PostgreSQL

连接串格式：

```env
COMPANY_POSTGRES_URL=postgresql+psycopg://readonly_user:password@host:5432/database
```

注意：如果部署 PostgreSQL，需要确认 Python 环境已安装对应驱动，例如 `psycopg` 或 `psycopg2-binary`。如果当前分支依赖未包含该驱动，需要先补依赖并重新构建。

### 6.3 SQLite

SQLite 分两类处理：

- BIRD 内置测试数据：默认位于 `data/bird/databases/`。
- 公司 SQLite 数据：不天然属于 BIRD 目录；是否放在该目录取决于当前代码的安全白名单策略。

当前 acceptance 分支的管理接口为了避免任意文件读取，对“手动注册 SQLite 文件”做了路径限制：注册路径必须解析到 `BIRD_DATA_DIR/databases` 下。因此，如果直接使用现有管理页面注册公司 SQLite 文件，需要把文件挂载或复制到该受控目录下，并使用相对路径，例如：

```text
company/company.sqlite
```

这不是 SQLite 数据源本身的部署要求，而是当前实现的安全边界。正式生产如果要接入公司 SQLite 文件，建议把“受控 SQLite 根目录”独立配置出来，例如 `SQLITE_DATA_SOURCE_ROOT=/data/askdata/sqlite-sources`，并把代码中的 SQLite 路径白名单从 BIRD 目录改为该专用目录。MySQL/PostgreSQL 公司数据源不受这个本地文件目录限制。

## 7. 配置项

在部署环境中配置以下变量。

### 7.1 后端配置

```env
LLM_API_BASE=https://api.deepseek.com
LLM_API_KEY=your_key
LLM_MODEL_NAME=deepseek-v4-pro
LLM_THINKING_ENABLED=true
LLM_REASONING_EFFORT=high

BIRD_DATA_DIR=data/bird
BIRD_INSTRUCTIONS_DIR=data/bird/instructions
ASKDATA_STATE_DIR=.checkpoints

ADMIN_API_TOKEN=change-this-token
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

QUERY_MAX_ROWS=1000
EXPORT_MAX_ROWS=10000
MAX_RESULT_BYTES=10485760
SQL_MAX_JOINS=8
SQL_MAX_SUBQUERY_DEPTH=4
SQL_STATEMENT_TIMEOUT_SECONDS=15
SLOW_QUERY_MS=2000

COMPANY_MYSQL_URL=mysql+pymysql://readonly_user:password@host:13306/database?charset=utf8mb4
COMPANY_POSTGRES_URL=postgresql+psycopg://readonly_user:password@host:5432/database
```

### 7.2 前端配置

```env
VITE_USER_ID=local-user
VITE_ADMIN_API_TOKEN=change-this-token
VITE_API_BASE_URL=/api
```

生产环境不应信任浏览器自报的 `VITE_USER_ID`。应由可信网关或 SSO 覆盖 `X-User-ID`。

## 8. 多平台部署

### 8.1 macOS 本地部署

适用场景：开发、验收、单机内部部署。

要求：

- Python 3.10+。
- `uv`。
- Node.js 22 或兼容版本。
- `npm`。
- 能访问 LLM API 和公司数据库。

步骤：

```bash
git clone https://github.com/Eric-miku/askdata-v1.git
cd askdata-v1
git switch feat/askdata-v1-acceptance
cp .env.example .env
```

编辑 `.env`，填写 LLM 和公司数据库连接。

启动后端：

```bash
bash scripts/setup-dev-env.sh
uv run askdata serve --host 127.0.0.1 --port 8000
```

启动前端：

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

访问：

- 前端：`http://localhost:5173`
- API 文档：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/health`

macOS 注意事项：

- 项目位于 iCloud/同步目录时，`.venv` 可能被系统隐藏或损坏。`scripts/setup-dev-env.sh` 已处理该问题，会使用 `venv.nosync`。
- 如果公司数据库在 VPN 内，先确认终端进程能访问 VPN 网络。

### 8.2 Linux 服务器部署

适用场景：测试服务器、内网服务器、长期运行服务。

要求：

- Python 3.10+。
- `uv`。
- Node.js 22 或兼容版本。
- `npm`。
- 进程管理工具，例如 `systemd`、`supervisor` 或容器运行时。
- 服务器能访问 LLM API 和公司数据库。

步骤：

```bash
git clone https://github.com/Eric-miku/askdata-v1.git
cd askdata-v1
git switch feat/askdata-v1-acceptance
cp .env.example .env
```

安装后端：

```bash
uv sync
uv run askdata --help
```

构建前端：

```bash
cd frontend
npm ci
npm run build
```

运行后端：

```bash
cd ..
uv run askdata serve --host 0.0.0.0 --port 8000
```

前端生产部署方式二选一：

- 用 Nginx 托管 `frontend/dist`。
- 使用 Docker Compose 中的 frontend 容器。

Linux 生产建议：

- 使用 Nginx/Caddy 终止 TLS。
- 反向代理 `/api` 到后端 `127.0.0.1:8000`。
- 由网关注入可信 `X-User-ID`。
- 限制 `/api/data-sources`、`/api/permissions`、`/api/knowledge` 管理入口。
- 将 `ASKDATA_STATE_DIR` 放在持久化磁盘。
- 配置日志采集和进程自动重启。

### 8.3 Windows 部署

适用场景：Windows 开发机、本地验收、内网 Windows 主机。

推荐方式：使用 WSL2 Ubuntu 部署。原因是项目脚本、Python 包、Node 构建和路径行为在 Linux 环境更稳定。

WSL2 步骤：

1. 安装 WSL2 和 Ubuntu。
2. 在 Ubuntu 中安装 Python、uv、Node.js、npm。
3. 在 WSL2 文件系统内 clone 项目，不建议放在 `/mnt/c/...`。
4. 按 Linux 部署步骤执行。

PowerShell 原生部署也可以，但需要自行处理：

- Python 虚拟环境。
- uv 安装。
- Node/npm 安装。
- 环境变量设置。
- 路径分隔符差异。
- 后端与前端进程管理。

PowerShell 环境变量示例：

```powershell
$env:LLM_API_BASE="https://api.deepseek.com"
$env:LLM_API_KEY="your_key"
$env:LLM_MODEL_NAME="deepseek-v4-pro"
$env:COMPANY_MYSQL_URL="mysql+pymysql://readonly_user:password@host:13306/database?charset=utf8mb4"
```

启动方式：

```powershell
uv sync
uv run askdata serve --host 127.0.0.1 --port 8000
```

另开 PowerShell：

```powershell
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

### 8.4 Docker Compose 部署

适用场景：可重复部署、服务器部署、隔离运行。

启动：

```bash
docker compose config
docker compose up --build
```

访问：

- 前端：`http://localhost:5173`
- 后端：`http://localhost:8000`

Docker 注意事项：

1. backend 容器默认从环境变量读取 LLM 配置。
2. `data/` 以只读卷挂载到 `/app/data`。
3. 容器内 `ASKDATA_STATE_DIR=/app/.checkpoints`，并使用 named volume `askdata-checkpoints` 持久化。
4. backend 根文件系统只读。
5. 临时目录使用 tmpfs。

如果要在 Docker 中使用公司数据源，确保公司数据库连接变量传入 backend 容器。建议在 `docker-compose.yml` 的 backend environment 中包含：

```yaml
COMPANY_MYSQL_URL: ${COMPANY_MYSQL_URL:-}
COMPANY_POSTGRES_URL: ${COMPANY_POSTGRES_URL:-}
```

如果 LLM 服务运行在宿主机，使用：

```env
LLM_API_BASE=http://host.docker.internal:9001/v1
```

Linux 下 Compose 已配置：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

## 9. 公司数据源接入

### 9.1 新增数据源

前端路径：

1. 打开 AskData。
2. 点击左侧数据库图标。
3. 点击“管理数据源”。
4. 填写数据源。

MySQL 示例：

- 数据源 ID：`company_mysql`
- 显示名称：`公司业务数据库`
- 类型：`MySQL`
- 连接配置：`env:COMPANY_MYSQL_URL`
- 启用：是

PostgreSQL 示例：

- 数据源 ID：`company_postgres`
- 显示名称：`公司 PostgreSQL`
- 类型：`PostgreSQL`
- 连接配置：`env:COMPANY_POSTGRES_URL`
- 启用：是

### 9.2 测试连接

点击“测试连接”。

成功条件：

- 状态显示“连接正常”。
- 表数量大于 0。
- 前端不展示真实密码。

失败排查：

- 环境变量是否存在。
- 后端是否重启。
- 数据库地址和端口是否可达。
- VPN 或代理是否影响连接。
- 只读账号是否有元数据权限。
- 密码中的特殊字符是否已 URL 编码。

### 9.3 同步 Schema

点击“同步 Schema”。

同步内容：

- 表。
- 列。
- 字段类型。
- 主键。
- 外键。
- 索引。
- Schema SHA-256 指纹。
- 结构变更摘要。

同步不会读取或保存业务数据行。

### 9.4 选择数据源查询

同步成功后，公司数据源会出现在数据库列表。用户选择该数据源后即可提问。

如果数据源不显示，通常原因是：

- 未启用。
- 未同步 Schema。
- 同步失败。
- 当前用户没有权限。

## 10. 权限配置

系统有两种权限状态：

1. 没有任何权限策略：本地兼容模式，默认允许。
2. 存在任意权限策略：白名单模式，只允许显式授权。

生产部署必须使用白名单模式。

数据源级授权：

- 用户 ID：`alice`
- 数据源 ID：`company_mysql`
- 表名：空
- 字段名：空
- 允许查询：是
- 允许导出：按需

表级授权：

- 用户 ID：`alice`
- 数据源 ID：`company_mysql`
- 表名：`orders`
- 字段名：空

字段级授权：

- 用户 ID：`alice`
- 数据源 ID：`company_mysql`
- 表名：`orders`
- 字段名：`amount`

行过滤示例：

```text
region = '华东'
```

行过滤会在后端 AST 层注入，适用于自然语言查询、SQL 回放和导出。展示给用户的 SQL 不包含内部行过滤文本。

## 11. 业务术语配置

公司数据通常存在口语和字段名不一致的问题。正式部署前应配置高频业务术语。

示例：

- 标准名：收入
- 别名：营收、销售额、GMV
- 定义：按已支付订单金额统计
- 映射：`company_mysql.orders.paid_amount`
- 聚合方式：`SUM`
- 状态：发布

发布后的术语会参与查询前的知识解析。如果同一个术语存在多个已发布且冲突的口径，系统会返回澄清问题，不直接生成 SQL。

## 12. 验证、运维与回滚

### 12.1 自动化测试

```bash
uv run pytest -q
```

如果时间有限，至少运行：

```bash
uv run pytest tests/test_data_source_store.py tests/test_query_runner.py tests/test_schema_catalog_routes.py tests/test_permissions.py -q
```

```bash
cd frontend
npm test -- --run
npm run build
```

### 12.2 健康和数据源验证

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/metrics
```

预期：

- `/health` 返回服务存活。
- `/ready` 返回数据目录就绪。
- `/metrics` 返回 Prometheus 文本指标。

检查项：

- 数据源创建成功。
- 连接测试成功。
- Schema 同步成功。
- Catalog 可查看。
- 数据源出现在数据库列表。
- 当前用户有权限。

### 12.3 查询验收

准备固定验收题集，至少覆盖：

- 单表筛选。
- 聚合统计。
- Top N。
- 日期筛选。
- Join。
- 业务术语。
- 权限受限字段。
- 空结果。
- 危险 SQL 拦截。

每个问题记录：

- 输入问题。
- 数据源 ID。
- 生成 SQL。
- 返回行数。
- 文字回答。
- 是否符合业务预期。
- 失败原因。

### 12.4 日志与监控

每个 HTTP 响应都包含 `X-Request-ID`。排障时应记录：

- request ID。
- session ID。
- user ID。
- database ID。
- SQL hash。
- retry count。
- elapsed ms。
- error code。

不要记录：

- 数据库密码。
- 完整连接串。
- 大量业务结果明文。
- 用户敏感字段。

基础接口：

- `GET /health`
- `GET /ready`
- `GET /metrics`

建议监控：

- 后端进程存活。
- 请求错误率。
- 查询耗时。
- SQL timeout 次数。
- `DB_ERROR` 次数。
- `SQL_BLOCKED` 次数。
- LLM 调用失败次数。
- 数据源健康状态。

### 12.5 备份和回滚

备份状态目录。统一以 `ASKDATA_STATE_DIR` 表示，默认值为 `.checkpoints`：

```bash
STATE_DIR="${ASKDATA_STATE_DIR:-.checkpoints}"
cp -R "$STATE_DIR" "$STATE_DIR.backup.$(date +%Y%m%d-%H%M%S)"
```

状态目录包含：

- 会话。
- 数据源配置。
- Schema Catalog。
- 权限策略。
- 业务术语。

公司数据库本身不由 AskData 备份。

代码回滚：

```bash
git switch feat/askdata-v1-acceptance
git pull
git checkout <last-known-good-commit>
```

重新启动服务。

Docker：

```bash
docker compose down
git checkout <last-known-good-commit>
docker compose up --build
```

状态回滚。停止服务后恢复 `ASKDATA_STATE_DIR`：

```bash
STATE_DIR="${ASKDATA_STATE_DIR:-.checkpoints}"
rm -rf "$STATE_DIR"
cp -R "$STATE_DIR.backup.YYYYMMDD-HHMMSS" "$STATE_DIR"
```

恢复后检查：

- `/health`
- `/ready`
- 数据源连接测试
- Schema Catalog
- 权限策略

## 13. 常见问题

### 13.1 模型没有执行 SQL

原因：

- 模型不支持 tool calling。
- 模型返回格式不符合 OpenAI-compatible Chat Completions。
- `LLM_THINKING_ENABLED` 与模型不兼容。

处理：

- 换支持 tool calling 的模型。
- 设置 `LLM_THINKING_ENABLED=false`。
- 查看 Trace 是否有 `run_query`。

### 13.2 公司数据源不显示

原因：

- 未同步 Schema。
- 数据源未启用。
- 连接失败。
- 当前用户无权限。

处理：

- 测试连接。
- 同步 Schema。
- 检查权限策略。

### 13.3 查询 SQL 不准

原因：

- 表名或字段名语义不明显。
- 公司数据源没有额外业务说明。
- 术语未发布。
- Schema Catalog 过期。

处理：

- 重新同步 Schema。
- 配置业务术语。
- 使用更明确的问题。
- 准备固定验收题集。

### 13.4 权限突然全部拒绝

原因：创建第一条权限策略后系统进入白名单模式。

处理：

- 给当前 `X-User-ID` 添加数据源级授权。
- 或删除全部权限策略，回到本地兼容模式。

### 13.5 Explain 不支持公司数据库

当前 `/api/query/explain` 只支持 SQLite。MySQL/PostgreSQL 可以执行自然语言查询，但执行计划接口会返回 422。

## 14. 演示准备

演示不是部署流程的一部分，但可以用部署后的系统完成。

演示前检查：

- 当前分支是 `feat/askdata-v1-acceptance`。
- `.env` 中 LLM 和公司数据库连接正确。
- 后端和前端均已启动。
- 公司数据源连接正常。
- Schema 已同步。
- 演示用户有权限。
- 业务术语已发布。
- 至少 3 个核心问题提前验证通过。
- 准备 1 个权限拦截或危险 SQL 拦截案例。

推荐演示顺序：

1. 打开数据源管理，展示公司数据源状态和 Catalog。
2. 选择公司数据源。
3. 提问一个简单筛选问题。
4. 提问一个聚合或 Top N 问题。
5. 展示生成 SQL、表格、图表和 Trace。
6. 展示业务术语或权限能力。

## 15. 生产化检查清单

正式生产前必须确认：

- 只读数据库账号已由 DBA 审核。
- 真实密钥不提交到 Git。
- `ADMIN_API_TOKEN` 已设置。
- 生产身份由可信网关或 SSO 注入。
- 前端不能伪造生产用户身份。
- `ASKDATA_STATE_DIR` 有持久化和备份策略。
- 日志不会泄露密码或大量业务数据。
- LLM 模型支持 tool calling。
- 固定业务题集通过验收。
- Docker 或服务器部署方式已验证。
- 回滚流程已验证。
