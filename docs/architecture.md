# AskData 系统架构

## 逻辑组件

```text
React/Vite UI
  |  X-User-ID / X-Admin-Token / X-Request-ID
FastAPI API
  |-- session manager -------- sessions.sqlite
  |-- knowledge store -------- knowledge.sqlite
  |-- data-source store ------ datasources.sqlite + schema_catalogs
  |-- permission store ------- permissions.sqlite
  |
  +-- QuestionUnderstanding
  +-- Knowledge resolution
  +-- SemanticRetriever ------ BIRD processed schema/instructions
  +-- AgentGraph
        |-- ReActSqlAgent / one-shot fallback
        |-- SQLValidator ----- sqlglot AST and complexity policy
        +-- SQLExecutor ------ read-only query, timeout and caps
  |
  +-- ChartRecommender / StructuredAnalyzer / Exporter
```

前端只消费受约束的图表规格，不执行模型生成的任意 ECharts JavaScript。后端以 snake_case 处理内部对象，现有 API 响应契约保持兼容。

## 查询时序

1. API 验证用户、会话和数据源归属。
2. 结构化理解提取对象、指标、维度、筛选、时间、排序和 TopN，并与上一轮上下文合并。
3. 已发布术语和别名先解析；冲突口径直接返回澄清问题。
4. SemanticRetriever 注入相关 Schema 和业务说明。
5. Agent 生成 SQL；权限上下文在任何执行前检查数据源、表和字段，并在内部 AST 上注入表级行过滤。
6. SQLValidator 阻止写操作、DDL、多语句、文件操作、系统对象及超限复杂度。
7. SQLExecutor 使用只读数据库、行数/字节/时间上限执行；失败时 Agent 在限制次数内修复。
8. API 生成确定性图表、分析证据和后续问题，并持久化结构化会话摘要。
9. 审计日志记录 request/trace/session/user/database ID、SQL 哈希、行数、重试数、耗时和错误码，不记录结果明文。

## 数据源与 Catalog

当前运行路径支持 BIRD 自动发现及管理员注册的 SQLite 文件。路径必须位于配置的 `BIRD_DATA_DIR/databases` 范围内，避免任意文件读取。

同步通过 SQLite 只读 URI 读取 `sqlite_master` 和 PRAGMA 元数据，生成稳定 Catalog：

- 表和原始 DDL。
- 列名、类型、可空性、默认值和主键位置。
- 主键、外键及更新/删除动作。
- 索引名、唯一性、来源、部分索引标记和列顺序。
- 表/列/索引计数和 canonical JSON 的 SHA-256 指纹。

每次同步保存当前和前一指纹，并列出新增、删除和内容变化的表。同步不修改业务库。

## SQL 治理

所有查询、回放、导出和执行计划入口共享 AST 权限与安全策略：

- 只允许单条 `SELECT`、`WITH ... SELECT` 或 `UNION`。
- 限制 JOIN 数和嵌套子查询深度。
- 拦截 SQLite 系统表和常见 MySQL/PostgreSQL 系统 Schema。
- 查询和导出采用不同最大行数；统一限制结果字节和墙钟时间。
- 慢查询在响应元数据和审计中产生告警。
- `EXPLAIN QUERY PLAN` 先验证和授权原 SQL，再通过只读连接执行。
- 索引候选由计划中的整表扫描与 WHERE/JOIN 字段确定，不提供自动执行 API。
- 行过滤表达式使用受限 AST 语法，受控表会被改写为带过滤条件的派生表；JOIN、CTE、回放、导出和执行计划不能绕过该改写。
- 外部响应、模型上下文和审计 SQL 哈希仍基于用户原 SQL，不暴露内部行策略文本。

AST 校验不能替代数据库最小权限。公司数据库接入时必须使用数据库侧只读账号。

## 持久化与恢复

`ASKDATA_STATE_DIR` 保存会话、LangGraph 检查点、术语版本、数据源/Catalog 和权限策略。备份该目录即可恢复 AskData 控制面状态；业务数据库由其自身备份体系负责。

现有 SQLite 状态表使用启动时兼容迁移，例如旧会话自动增加 `user_id`。恢复后应依次检查 `/health`、`/ready`、数据源连接测试和权限场景。

## 部署拓扑

Compose 包含非 root FastAPI 和 Nginx 前端容器。根文件系统只读，临时目录使用 tmpfs，业务数据只读挂载，状态使用命名卷。模型通过 OpenAI-compatible API 连接，默认容器地址可由 `LLM_API_BASE` 覆盖。

生产拓扑应在 FastAPI 前放置可信网关：终止 TLS、完成 SSO、覆盖 `X-User-ID`、限制管理员入口并生成或透传 request ID。

## 外部扩展点

- MySQL/PostgreSQL：通用凭据引用、方言 Catalog、驱动和连接池配置。
- 身份：SSO、角色组同步、可信身份声明，以及由身份属性动态生成行策略。
- 数据治理：敏感字段脱敏/审批、公司审计平台和凭据管理器。
- 分析：经过业务验证的时间序列库和特征质量门禁。
- 可观测性：集中日志、Prometheus 抓取和告警规则。
