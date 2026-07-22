# AskData 部署与运维

## 本地运行

```bash
bash scripts/setup-dev-env.sh
uv run askdata serve
cd frontend && npm install && npm run dev
```

访问前端 `http://localhost:5173`、API 文档 `http://localhost:8000/docs`。

## 必要配置

复制 `.env.example` 为 `.env`，至少配置可访问的 OpenAI-compatible 模型：

```env
LLM_API_BASE=https://api.deepseek.com
LLM_API_KEY=
LLM_MODEL_NAME=deepseek-v4-pro
LLM_THINKING_ENABLED=true
LLM_REASONING_EFFORT=high
BIRD_DATA_DIR=data/bird
ASKDATA_STATE_DIR=.checkpoints
QUERY_MAX_ROWS=1000
EXPORT_MAX_ROWS=10000
MAX_RESULT_BYTES=10485760
SQL_MAX_JOINS=8
SQL_MAX_SUBQUERY_DEPTH=4
SQL_STATEMENT_TIMEOUT_SECONDS=15
SLOW_QUERY_MS=2000
```

生产环境应设置随机的 `ADMIN_API_TOKEN`。前端构建时设置相同的 `VITE_ADMIN_API_TOKEN`，或由网关注入 `X-Admin-Token`。查询身份由 `X-User-ID` 指定，本地前端使用 `VITE_USER_ID`；生产环境必须由可信网关/SSO 覆盖该请求头，不能信任浏览器自行声明。不要把真实令牌写入仓库。

## Docker Compose

```bash
docker compose config
docker compose up --build
```

Compose 默认通过 `host.docker.internal:9001` 访问宿主机模型服务，可使用 `LLM_API_BASE` 覆盖。DeepSeek 思考模式还需设置 `LLM_THINKING_ENABLED=true` 和 `LLM_REASONING_EFFORT=high`。`data/` 以只读卷挂载，`.checkpoints` 使用持久卷保存会话、术语和数据源元数据。

## 运维接口

| 接口 | 用途 |
| --- | --- |
| `GET /health` | 进程存活 |
| `GET /ready` | BIRD databases 目录就绪 |
| `GET /metrics` | Prometheus 文本指标 |
| `GET /api/metadata/databases` | 当前启用且可查询的数据源 |
| `GET /api/data-sources` | 管理数据源健康与同步状态 |
| `POST /api/data-sources/{id}/sync` | 同步 SQLite Catalog、结构指纹和变更摘要 |
| `GET /api/data-sources/{id}/schema` | 读取最近一次持久化的表、列、PK/FK 和索引 Catalog |
| `GET/POST/DELETE /api/permissions` | 管理用户的数据源、表、字段和导出白名单 |
| `POST /api/query/explain` | 在当前用户对象权限下读取 SQLite 执行计划和人工索引建议 |
| `POST /api/knowledge/import` | 最多 1000 条 JSON 术语/指标批量 upsert 和逐条错误报告 |
| `GET /api/knowledge/export` | 导出 JSON 或 CSV 知识快照 |

所有响应携带 `X-Request-ID`。会话和查询接口按 `X-User-ID` 隔离；生产环境必须由可信网关注入该值。排障时用 request ID、trace ID、session ID、user ID、database ID、SQL hash 和错误码串联日志，禁止记录数据库凭据、SQL 参数和结果明文。

## 备份和恢复

备份 `ASKDATA_STATE_DIR` 下的 SQLite 文件即可保存会话、LangGraph 检查点、业务术语版本、管理数据源配置和权限策略。BIRD/公司数据库本身不由 AskData 备份。恢复时先停止服务，恢复状态目录，再启动并检查 `/ready`。

## 发布检查

```bash
uv run pytest -q
cd frontend && npm test -- --run
cd frontend && npm run build
```

随后验证危险 SQL 拦截、管理员令牌、导出公式注入、数据源禁用、Schema 变更检测、执行计划权限、多用户会话隔离和真实业务题集指标。
