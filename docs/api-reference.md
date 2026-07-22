# AskData API 参考

默认地址为 `http://localhost:8000`，交互式 OpenAPI 文档位于 `/docs`。除健康接口外，业务接口统一使用 `/api` 前缀。

## 请求头

| 请求头 | 适用范围 | 说明 |
| --- | --- | --- |
| `X-User-ID` | 会话、元数据、查询、回放、导出、执行计划 | 本地默认 `local-user`；生产必须由可信网关注入 |
| `X-Admin-Token` | 数据源、权限、术语写操作和交换 | 仅配置 `ADMIN_API_TOKEN` 后强制校验 |
| `X-Request-ID` | 所有接口 | 可由调用方提供；否则服务生成，响应总会返回 |

## 运维接口

| 方法与路径 | 响应 |
| --- | --- |
| `GET /health` | 进程存活状态 |
| `GET /ready` | BIRD databases 目录是否就绪；未就绪返回 503 |
| `GET /metrics` | Prometheus 文本指标 |

## 元数据与会话

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/metadata/databases` | 当前用户有权查询的数据源，不返回文件路径 |
| `GET /api/metadata/{database_id}/tables` | 经过表/字段权限过滤的列结构 |
| `POST /api/sessions?database_id={id}` | 创建当前用户会话 |
| `GET /api/sessions?limit=50&offset=0` | 当前用户会话列表 |
| `GET /api/sessions/{session_id}` | 会话与历史详情 |
| `PATCH /api/sessions/{session_id}` | 更新会话数据源 |
| `POST /api/sessions/{session_id}/reset` | 清空历史和结构化上下文 |
| `DELETE /api/sessions/{session_id}` | 删除会话 |

其他用户的会话 ID 始终按不存在处理，返回 404。

## 查询、回放与导出

`POST /api/query`：

```json
{
  "database_id": "sales",
  "question": "上个月按部门查看销售额前5名",
  "session_id": "optional-session-id"
}
```

响应包含 `answer`、`sql`、`columns`、`rows`、`chart`、`analysis`、`suggestions`、`trace` 和 `error`。歧义问题可能返回澄清回答而不执行 SQL。

`POST /api/query/execute-sql` 用于历史只读 SQL 回放：

```json
{"database_id": "sales", "sql": "SELECT department, SUM(amount) FROM orders GROUP BY department"}
```

`POST /api/query/export` 在服务端重新执行 SQL：

```json
{
  "database_id": "sales",
  "question": "按部门查看销售额",
  "sql": "SELECT department, SUM(amount) AS amount FROM orders GROUP BY department",
  "format": "xlsx"
}
```

`format` 为 `csv` 或 `xlsx`。PNG 由浏览器从当前 ECharts 实例生成。

`POST /api/query/explain` 使用与回放相同请求体，返回：

```json
{
  "success": true,
  "normalized_sql": "SELECT region FROM orders WHERE region = '华东'",
  "plan": [{"id": 2, "parent": 0, "detail": "SCAN orders"}],
  "suggestions": [{
    "type": "index_candidate",
    "table": "orders",
    "columns": ["region"],
    "reason": "执行计划显示筛选或关联条件正在扫描整表",
    "sql": "CREATE INDEX ...",
    "automatic": false
  }],
  "warnings": ["索引建议仅供管理员评估，AskData 不会自动修改数据库索引"]
}
```

## 数据源管理

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/data-sources` | 列出管理数据源、健康、同步、指纹和变更状态 |
| `POST /api/data-sources` | 注册受控 SQLite 数据源 |
| `PUT /api/data-sources/{id}` | 更新名称、路径和启用状态 |
| `PATCH /api/data-sources/{id}/status` | 启用或禁用 |
| `POST /api/data-sources/{id}/test` | 只读连接测试 |
| `POST /api/data-sources/{id}/sync` | 同步并持久化 Schema Catalog |
| `GET /api/data-sources/{id}/schema` | 读取最近 Catalog、指纹和变更摘要 |
| `DELETE /api/data-sources/{id}` | 删除控制面配置和 Catalog，不删除业务库文件 |

数据源请求示例：

```json
{"id": "sales", "name": "销售数据库", "path": "sales/sales.sqlite", "enabled": true}
```

路径必须位于配置的 BIRD databases 目录内。

## 权限管理

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/permissions?user_id=alice` | 查询策略 |
| `POST /api/permissions` | 新增或更新同一范围策略 |
| `DELETE /api/permissions/{policy_id}` | 撤销策略 |

```json
{
  "user_id": "alice",
  "database_id": "sales",
  "table_name": "orders",
  "field_name": "amount",
  "can_query": true,
  "can_export": false,
  "row_filter": null
}
```

字段策略必须同时指定表。`row_filter` 只能配置在表级策略，例如 `region = '华东'`，最长 1000 字符；函数、子查询、多语句和跨表引用会返回 422。行策略适用于自然语言查询、SQL 回放、导出和执行计划，但不会拼入对普通用户展示的 SQL。配置任意策略后启用全局白名单模式。

## 术语知识管理

主要接口为 `/api/knowledge/entries` CRUD、`/{id}/publish`、`/{id}/versions`、`/{id}/rollback/{version}`、`/import` 和 `/export?format=json|csv`。批量导入请求：

```json
{"mode": "upsert", "entries": [{"kind": "term", "standard_name": "客户", "aliases": ["用户"]}]}
```

导入项强制进入草稿，避免绕过发布时字段映射校验。

## 错误语义

Pydantic 输入错误返回 422；不存在或被隐藏的资源返回 404；权限拒绝通常返回 403；危险或无效 SQL 返回 400。未处理异常统一返回：

```json
{
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "服务暂时无法处理请求，请稍后重试",
    "request_id": "..."
  }
}
```

SQL 执行内部稳定码包括 `SQL_BLOCKED`、`PERMISSION_DENIED`、`DB_NOT_FOUND`、`DB_ERROR`、`TIMEOUT` 和 `RESULT_TOO_LARGE`。内部异常细节只写服务日志。
