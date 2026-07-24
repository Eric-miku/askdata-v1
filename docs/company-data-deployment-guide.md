# AskData 公司数据部署文档

本文档面向答辩演示和接近生产形态的公司数据部署。目标是把 AskData 后端、前端、OpenAI-compatible 大模型服务和公司只读数据库连接起来，并提供可重复的部署、验证、回滚和排障流程。

AskData 的特殊点是：核心功能依赖支持 tool calling 的大模型；公司数据库必须通过只读账号接入；AskData 本地只保存控制面状态和 Schema Catalog，不保存公司业务数据行。

## 1. 部署范围

本次部署包含：

- FastAPI 后端：提供 `/api/query`、数据源管理、权限、会话、术语、导出和运维接口。
- React/Vite 前端：提供问数界面、数据库选择、数据源管理、权限管理、术语管理、结果表格和图表。
- OpenAI-compatible LLM：必须支持 Chat Completions 和 `tool_calls`。
- 公司数据库：MySQL 或 PostgreSQL，使用只读 SQLAlchemy 连接串接入。
- 本地状态目录：`.checkpoints` 或 `ASKDATA_STATE_DIR`，保存会话、数据源配置、Schema Catalog、权限和术语。

本次部署不包含：

- 公司数据库备份。
- 公司数据库 Schema 变更发布。
- 自动创建索引或自动修改业务库结构。
- 生产 SSO 网关实现。生产环境应由网关注入可信 `X-User-ID`。

## 2. 架构

```text
Browser
  |
  | X-User-ID / X-Admin-Token
  v
React Frontend
  |
  | /api/*
  v
FastAPI Backend
  |-- DataSourceStore: datasources.sqlite + schema_catalogs
  |-- PermissionStore: permissions.sqlite
  |-- KnowledgeStore: knowledge.sqlite
  |-- SessionManager: sessions/checkpoints
  |
  |-- SemanticRetriever
  |     |-- BIRD processed schema
  |     |-- synced company Schema Catalog
  |
  |-- ReActSqlAgent
  |     |-- LLM.Chat(tools=[run_query])
  |     |-- query_runner.Execute(sql, "source:<data_source_id>")
  |
  |-- SQLExecutor
        |-- SQLValidator
        |-- read-only database connection
        |-- row/byte/time limits
        |-- MySQL/PostgreSQL/SQLite dialect execution
```

公司数据源接入后，查询路径是：

1. 管理员在前端新增数据源，路径使用 `env:COMPANY_MYSQL_URL` 或 `env:COMPANY_POSTGRES_URL`。
2. 后端从 `.env` 或系统环境变量读取真实连接串。
3. 管理员点击“测试连接”。
4. 管理员点击“同步 Schema”。
5. 后端只读取表、列、主键、外键、索引和结构指纹，保存到本地 Catalog。
6. 数据库选择器显示该公司数据源。
7. 用户提问时，Retriever 使用 Catalog 构造 Schema Prompt。
8. LLM 通过 tool calling 调用 `run_query(sql)`。
9. 后端执行安全校验、权限校验、行过滤、只读 SQL 执行。
10. 前端展示回答、SQL、结果表、图表、分析和 Trace。

## 3. 环境要求

### 3.1 本地演示环境

- macOS 或 Linux。
- Python 3.10+；当前 acceptance 分支 Dockerfile 使用 Python 3.13。
- `uv`。
- Node.js 22 或兼容版本。
- `npm`。
- 能访问大模型 API。
- 能访问公司数据库网络地址。

### 3.2 公司数据库要求

公司数据库账号必须是只读账号。最小权限建议：

- 允许 `SELECT`。
- 允许读取元数据表或 information schema，用于 Schema 同步。
- 禁止 `INSERT`、`UPDATE`、`DELETE`、`DROP`、`ALTER`、`CREATE`、`TRUNCATE`。
- 设置数据库侧查询超时更好；AskData 后端也有应用层超时保护。

MySQL SQLAlchemy URL 示例：

```env
COMPANY_MYSQL_URL=mysql+pymysql://readonly_user:password@host:13306/database?charset=utf8mb4
```

PostgreSQL SQLAlchemy URL 示例：

```env
COMPANY_POSTGRES_URL=postgresql+psycopg://readonly_user:password@host:5432/database
```

注意：当前 `pyproject.toml` 已包含 `pymysql`，MySQL 更适合作为演示路径。PostgreSQL 如果使用 `psycopg`，需要确认依赖已安装。

## 4. 大模型要求

AskData 默认核心链路是 ReAct tool-calling Agent。模型必须满足：

- 提供 OpenAI-compatible Chat Completions API。
- 支持 `tools` 参数。
- 响应中能返回 `message.tool_calls`。
- 工具调用参数能稳定输出 JSON，例如 `{"sql": "SELECT ..."}`。

不满足 tool calling 的模型会导致 Agent 无法真正调用 `run_query`，常见表现是：

- 模型只输出 SQL 文本，但后端没有执行 SQL。
- Trace 中没有正常的工具调用步骤。
- 查询返回空结果或通用失败信息。

推荐配置：

```env
LLM_API_BASE=https://api.deepseek.com
LLM_API_KEY=your_key
LLM_MODEL_NAME=deepseek-v4-pro
LLM_THINKING_ENABLED=true
LLM_REASONING_EFFORT=high
```

如果模型接口不支持 `thinking` 扩展参数，应改为：

```env
LLM_THINKING_ENABLED=false
```

## 5. 必要配置

在项目根目录创建 `.env`：

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
```

前端本地开发需要：

```env
VITE_USER_ID=local-user
VITE_ADMIN_API_TOKEN=change-this-token
```

如果不设置 `ADMIN_API_TOKEN`，管理接口在本地开发模式下开放。演示公司数据时建议设置，避免误操作。

## 6. 本地一键启动

从项目根目录执行：

```bash
bash start-askdata-demo.sh
```

脚本会执行：

- 检查 `python3`、`uv`、`npm`。
- 如果缺少虚拟环境，运行 `scripts/setup-dev-env.sh`。
- 如果缺少前端依赖，运行 `npm install`。
- 启动后端：`uv run askdata serve --host 127.0.0.1 --port 8000`。
- 启动前端：`npm run dev -- --host 0.0.0.0 --port 5173`。
- 打开 `http://localhost:5173`。
- 日志写入 `.logs/demo-backend.log` 和 `.logs/demo-frontend.log`。

手动启动方式：

```bash
bash scripts/setup-dev-env.sh
uv run askdata serve --host 127.0.0.1 --port 8000
```

另开终端：

```bash
cd frontend
npm install
npm run dev
```

访问：

- 前端：`http://localhost:5173`
- API 文档：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/health`
- 就绪检查：`http://localhost:8000/ready`

## 7. 公司数据源接入步骤

### 7.1 打开数据源管理

1. 打开前端。
2. 点击左侧数据库图标。
3. 点击“管理数据源”。

### 7.2 新增 MySQL 数据源

填写：

- 数据源 ID：`company_mysql`。
- 显示名称：`公司业务数据库`。
- 类型：`MySQL`。
- 连接配置：`env:COMPANY_MYSQL_URL`。
- 启用：是。

保存后，AskData 本地状态库只保存 `env:COMPANY_MYSQL_URL`，不保存真实密码。

### 7.3 测试连接

点击“测试连接”。

成功条件：

- 状态显示“连接正常”。
- 表数量大于 0。
- 没有暴露真实连接串密码。

失败时检查：

- `.env` 中是否有 `COMPANY_MYSQL_URL`。
- 后端进程是否能读取 `.env`。
- 数据库 host、port、账号、密码是否正确。
- 当前网络、VPN、代理是否能访问公司数据库。
- 数据库账号是否有元数据读取权限。

### 7.4 同步 Schema

点击“同步 Schema”。

成功后，AskData 会持久化：

- 表名。
- 字段名。
- 字段类型。
- 主键。
- 外键。
- 索引。
- Schema 指纹。
- Schema 变更摘要。

同步不会读取或保存业务数据行。

### 7.5 选择公司数据源提问

同步完成后：

1. 回到数据库抽屉。
2. 选择 `公司业务数据库`。
3. 输入业务问题。

前端会优先自动选择 MySQL/Postgres 公司数据源；如果用户手动选择了其他数据源，则以用户选择为准。

## 8. 权限配置

系统默认是本地兼容模式：没有任何权限策略时，允许所有用户访问所有数据源。

一旦创建任意权限策略，系统进入白名单模式。之后用户只能访问显式授权的数据源、表和字段。

演示前建议二选一：

### 方案 A：不配置权限策略

适合快速答辩演示。风险是本地所有前端用户默认都可访问已启用数据源。

### 方案 B：配置演示用户权限

前端默认用户：

```env
VITE_USER_ID=local-user
```

在权限管理中新增：

- 用户 ID：`local-user`
- 数据源 ID：`company_mysql`
- 表名：留空，表示整个数据源
- 字段名：留空
- 允许查询：是
- 允许导出：按演示需要

如果要限制到表：

- 表名：`orders`
- 字段名：留空

如果要限制到字段：

- 表名：`orders`
- 字段名：`amount`

注意：字段级权限会阻止 `SELECT *`，这是预期行为。

行过滤示例：

```text
region = '华东'
```

行过滤只支持受限表达式，不支持函数、子查询、多语句或跨表引用。

## 9. 业务术语配置

公司数据通常存在口语和字段名不一致的问题。例如用户说“收入”，数据库字段叫 `paid_amount` 或 `net_revenue`。

演示前应打开“业务术语管理”，配置高频术语：

- 标准名：收入
- 别名：营收、销售额、GMV
- 定义：按已支付订单金额统计
- 字段映射：`company_mysql.orders.paid_amount`
- 聚合方式：`SUM`
- 状态：发布

发布后的术语会在查询前被 `_resolve_knowledge` 注入问题上下文，帮助 LLM 生成更准确的 SQL。

如果同一术语存在多个已发布且冲突的口径，系统会返回澄清问题，而不是直接生成 SQL。

## 10. 验证清单

### 10.1 部署前验证

```bash
uv run pytest -q
cd frontend
npm test -- --run
npm run build
```

如果时间有限，至少验证：

```bash
uv run pytest tests/test_data_source_store.py tests/test_query_runner.py tests/test_schema_catalog_routes.py -q
cd frontend && npm run build
```

### 10.2 服务验证

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/metrics
```

预期：

- `/health` 返回 `{"status":"ok"}`。
- `/ready` 返回 ready；如果 BIRD 目录缺失会返回 503，但公司数据源功能仍依赖管理数据源同步。
- `/metrics` 返回 Prometheus 文本。

### 10.3 数据源验证

在前端执行：

1. 新增数据源。
2. 测试连接。
3. 同步 Schema。
4. 查看 Catalog。
5. 回到数据库选择器确认公司数据源出现。

也可用 API：

```bash
curl -H "X-Admin-Token: change-this-token" http://localhost:8000/api/data-sources
```

### 10.4 查询验证

准备 5 到 10 个确定答案的问题：

- 单表筛选。
- 聚合统计。
- Top N。
- 时间范围。
- Join。
- 术语别名。
- 权限受限字段。

每个问题检查：

- 是否选中了公司数据源。
- Trace 是否包含 Schema 检索、SQL 生成、执行。
- SQL 是否只包含 SELECT。
- 结果行数是否合理。
- 回答是否只基于查询结果。
- 图表是否匹配数据形态。

### 10.5 安全验证

验证危险 SQL 被阻止：

```json
{"database_id":"company_mysql","sql":"DROP TABLE orders"}
```

预期：

- 返回 `SQL_BLOCKED` 或权限拒绝。
- 公司数据库没有任何结构变化。

验证权限：

- 未授权用户看不到数据源。
- 未授权字段查询返回 403。
- 导出权限关闭时不能导出。

## 11. Docker 部署注意事项

当前 `docker-compose.yml` 已提供 backend/frontend 服务，但公司数据库演示有一个额外注意点：

- Compose backend environment 当前列出了 LLM、BIRD、ADMIN、CORS、状态目录。
- 如果要在 Docker 中使用 `env:COMPANY_MYSQL_URL`，需要确保该环境变量进入 backend 容器。

建议在 `docker-compose.yml` 的 backend environment 增加：

```yaml
COMPANY_MYSQL_URL: ${COMPANY_MYSQL_URL:-}
COMPANY_POSTGRES_URL: ${COMPANY_POSTGRES_URL:-}
```

然后执行：

```bash
docker compose config
docker compose up --build
```

容器部署访问：

- 前端：`http://localhost:5173`
- 后端：`http://localhost:8000`

如果模型服务在宿主机，默认使用：

```env
LLM_API_BASE=http://host.docker.internal:9001/v1
```

如果在 Linux 上 `host.docker.internal` 不可用，Compose 已配置：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

## 12. 发布策略

推荐采用小步、可回滚的部署策略：

1. 先部署到本地或测试环境。
2. 使用公司只读库的测试账号验证连接。
3. 同步 Schema。
4. 跑固定验收题集。
5. 确认权限策略。
6. 切换给演示用户使用。
7. 保留旧版本代码和 `.checkpoints` 备份，便于回滚。

对于答辩演示，不建议临场修改：

- 数据库连接串。
- 权限白名单。
- LLM 模型。
- 大量业务术语。
- Docker 网络配置。

## 13. 回滚方案

### 13.1 应用回滚

如果新代码不可用：

```bash
git switch feat/askdata-v1-acceptance
git pull
bash start-askdata-demo.sh
```

如果使用 Docker：

```bash
docker compose down
git checkout <last-known-good-commit>
docker compose up --build
```

### 13.2 控制面状态回滚

AskData 控制面状态位于：

```text
.checkpoints/
```

包含：

- 数据源配置。
- Schema Catalog。
- 权限策略。
- 业务术语。
- 会话历史。

备份：

```bash
cp -R .checkpoints ".checkpoints.backup.$(date +%Y%m%d-%H%M%S)"
```

恢复：

```bash
rm -rf .checkpoints
cp -R .checkpoints.backup.YYYYMMDD-HHMMSS .checkpoints
```

恢复后重启后端，并重新检查：

- `/health`
- `/ready`
- 数据源连接测试
- Schema Catalog
- 权限策略

## 14. 排障手册

### 14.1 公司数据源没有出现在数据库列表

原因通常是：

- 数据源未启用。
- 没有同步 Schema。
- 同步失败。
- 当前用户没有权限。

处理：

1. 打开数据源管理。
2. 查看健康状态。
3. 点击“测试连接”。
4. 点击“同步 Schema”。
5. 检查权限策略。

### 14.2 连接测试失败

检查：

- `.env` 是否存在。
- `COMPANY_MYSQL_URL` 是否拼写正确。
- 数据源路径是否填写 `env:COMPANY_MYSQL_URL`。
- 后端是否重启过。
- 网络、VPN、代理是否可达数据库。
- 账号是否只读但有 metadata 权限。
- 密码中是否有特殊字符需要 URL 编码。

### 14.3 查询失败但连接正常

检查：

- 模型是否支持 tool calling。
- `LLM_THINKING_ENABLED` 是否与模型兼容。
- Trace 中是否有 `run_query` 调用。
- SQL 是否引用了不存在的表或字段。
- Schema Catalog 是否过期，需要重新同步。
- 权限是否阻止了字段或表。
- 查询是否超时或结果过大。

### 14.4 模型生成 SQL 不准

处理优先级：

1. 重新同步 Schema，确认表和字段完整。
2. 添加业务术语映射。
3. 添加数据库说明或字段含义。
4. 使用更明确的演示问题。
5. 固定答辩题集，提前验证。

当前 acceptance 分支对公司数据源主要依赖 Catalog 的表名、字段名和业务术语；没有为公司数据源单独建立 Milvus 向量索引。因此字段名语义不明显时，业务术语配置很关键。

### 14.5 权限突然全部拒绝

原因：创建第一条权限策略后，系统从“默认允许”切换为“白名单模式”。

处理：

- 给 `VITE_USER_ID` 对应用户补充数据源级授权。
- 或删除所有权限策略，回到本地兼容模式。

### 14.6 Explain 不支持公司数据源

当前 `/api/query/explain` 只支持 SQLite。MySQL/PostgreSQL 公司数据源查询可以执行，但执行计划接口会返回 422。这不影响核心 NL2SQL 演示。

## 15. 演示前最终检查

演示前 30 分钟执行：

```bash
git status -sb
bash start-askdata-demo.sh
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

在前端检查：

- 公司数据源显示在数据库列表顶部。
- 数据源状态为“连接正常”。
- Schema 已同步。
- 演示用户有权限。
- 3 个核心问题能稳定返回 SQL 和结果。
- 1 个术语问题能命中业务术语。
- 1 个危险 SQL 或无权限场景能被拦截。

## 16. 生产化差距

当前分支已经可以做公司数据演示，但正式生产还应补齐：

- 可信网关或 SSO，禁止浏览器自报 `X-User-ID`。
- 密钥管理系统，不把数据库连接串放普通 `.env`。
- 集中日志和告警。
- Prometheus/Grafana 指标采集。
- 固定业务验收题集和准确率报告。
- PostgreSQL 驱动依赖确认。
- Docker Compose 中显式透传公司数据库环境变量。
- 公司 Schema 变更后的同步流程和审批流程。

