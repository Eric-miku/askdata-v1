# AskData 计划书需求追踪与验收矩阵

本矩阵把原始《项目计划书》和《项目计划书未完成部分实施计划》中的交付项映射到代码、测试和可重复验收命令。状态含义为：已完成（当前仓库可验证）、部分完成（核心链路可跑，仍缺企业环境能力）、待完成（尚无实现）。

| 编号 | 计划书要求 | 当前状态 | 实现/证据 | 验收方法 |
| --- | --- | --- | --- | --- |
| NLQ-01 | 自然语言生成只读 SQL 并执行 | 已完成 | `backend/askdata/agent`、`db/validator.py`、`POST /api/query` | `uv run pytest -q tests/test_agent_graph.py tests/test_query_route.py tests/test_query_runner.py` |
| NLQ-02 | Schema、业务上下文、SQL 修复 | 已完成 | `tools/retriever.py`、`agent/react_sql_agent.py`、skills | `uv run pytest -q tests/test_retriever.py tests/test_react_sql_agent.py` |
| NLQ-03 | 结构化理解、错误可理解、超时/分页和只读优化建议 | 已完成（规则理解范围） | `agent/understanding.py` 输出对象、指标、维度、筛选、时间、排序和 TopN；`db/optimizer.py` 提供受权限保护的 SQLite 执行计划和非自动索引建议 | `uv run pytest -q tests/test_understanding.py tests/test_runner.py tests/test_answer_shape.py tests/test_query_optimizer.py` |
| NLQ-04 | 多轮继承、覆盖、清除、切换数据源和用户隔离 | 已完成（核心场景） | 结构化检查点支持时间/指标/维度/筛选继承、覆盖与显式清除；数据源切换清理旧 SQL；持久会话按 `X-User-ID` 隔离 | `uv run pytest -q tests/test_understanding.py tests/test_session_manager.py tests/test_session_routes.py tests/test_query_route.py` |
| SEC-01 | 写操作、多语句、危险 SQL 100% 拦截 | 已完成（SQLite 回归范围） | `db/validator.py`、所有查询/导出入口复用 `Execute` | `uv run pytest -q tests/test_query_runner.py tests/test_paths.py` |
| SEC-02 | 用户、数据源、表、字段、行级和导出权限 | 已完成（本地用户策略范围） | `security/permissions.py`、`X-User-ID`、元数据过滤、执行前 SQL AST 授权、受限行过滤 AST、权限管理 API/页面；查询、回放、导出和执行计划共享策略 | `uv run pytest -q tests/test_permissions.py` |
| SEC-03 | SQL 复杂度、系统表、超时、行数、字节和慢查询治理 | 已完成（SQLite 执行范围） | 单语句、JOIN/子查询深度、系统对象、模式行数、结果字节、墙钟超时、慢查询警告和稳定错误码 | `uv run pytest -q tests/test_sql_governance.py tests/test_query_runner.py` |
| VIS-01 | 表格、柱状、条形、折线、饼图推荐 | 已完成 | `tools/visualization.py`、`frontend/src/utils/chartBuilder.ts` | `uv run pytest -q tests/test_visualization_analysis_export.py`; `cd frontend && npm test -- --run` |
| VIS-02 | 手动切换、历史与新查询一致 | 已完成 | `ResultChart` 类型选择；回放使用后端 chart | 前端 QueryResultView/Store 测试 |
| VIS-03 | CSV、XLSX、PNG 导出 | 已完成（结果重放权限范围） | `POST /api/query/export`、`tools/exporter.py`、ECharts `getDataURL` | 导出 API 集成测试；浏览器点击验证文件可打开 |
| ANA-01 | 同比/环比/占比/趋势/TopN 结构化分析 | 已完成（规则分析范围） | `tools/analysis.py` 提供范围、排行、占比、相邻周期、同比及逐行计算证据 | `uv run pytest -q tests/test_visualization_analysis_export.py` |
| ANA-02 | 异常检测与预测不编造原因 | 已完成（轻量预测） | IQR 异常、OLS 趋势预测、训练窗口和 95% 参考区间；明确预测不是事实 | 同上 |
| ANA-03 | 关联分析问题推荐 | 已完成（规则候选） | `StructuredAnalyzer.Suggest`、前端可点击继续提问 | 查询响应 `suggestions` 字段与前端测试 |
| TERM-01 | 术语、别名、指标、字段映射、版本 CRUD 与批量交换 | 已完成 | SQLite 知识库、CRUD/搜索/发布/版本/回滚、JSON 批量 upsert 错误报告、JSON/CSV 导出，以及前端维护页面 | `uv run pytest -q tests/test_knowledge_store.py`; 前端 API/构建测试 |
| TERM-02 | 术语发布前字段校验、查询匹配和冲突澄清 | 已完成（本地索引） | 发布校验真实库表字段；已发布别名注入查询；冲突口径触发澄清 | `uv run pytest -q tests/test_knowledge_store.py tests/test_query_route.py` |
| DATA-01 | SQLite/BIRD 数据源发现与 Schema 展示 | 已完成（只读发现） | `GET /api/metadata/databases`、`/{id}/tables` | `uv run pytest -q tests/test_query_route.py tests/test_paths.py` |
| DATA-02 | 在线接入、连接测试、Schema Catalog 同步、变更检测、启停和删除 | 已完成（受控 SQLite 范围） | `/api/data-sources` 生命周期 API；同步持久化 DDL、列、PK/FK、索引、SHA-256 指纹和对象级变更摘要；管理页可展开 Catalog。SQLite 无原生字段注释元数据；公司 MySQL/PostgreSQL 仍是增强项 | `uv run pytest -q tests/test_data_source_store.py tests/test_schema_catalog_routes.py` |
| ENG-01 | 健康、就绪、指标、结构化请求/审计日志和安全异常 | 已完成 | `/health`、`/ready`、`/metrics`、request-id 上下文、查询审计字段、统一安全 500 响应 | `uv run pytest -q tests/test_operations.py tests/test_query_route.py` |
| ENG-02 | Docker Compose 可重复部署 | 部分完成 | 非 root 前后端镜像、Compose、健康检查和状态卷已提供；当前机器无 Docker CLI，尚待外部构建验证 | `docker compose config`、`docker compose up --build` |
| ENG-03 | 产品、架构、API、开发、测试、部署和发布流程文档 | 已完成 | `docs/product-guide.md`、`architecture.md`、`api-reference.md`、`development-and-testing.md`、`deployment-and-operations.md`、`release-process.md`；README 提供统一入口 | 检查文档内命令、路由和本地链接；按开发与发布指南执行门禁 |
| ENG-04 | 固定题集、机器验收和回归指标 | 部分完成（外部模型待复测） | 固定 BIRD 100 题 manifest、机器可读核心多轮/安全/Catalog/执行计划场景和阈值、历史报告 | `uv run pytest -q tests/test_acceptance_manifest.py`; `uv run askdata eval-bird ...` |

## 固定核心验收场景

1. 选择 BIRD SQLite 数据库，提问“按月份查看销售额趋势”，确认返回只读 SQL、结果表、折线图、趋势证据和关联问题。
2. 点击“饼图/条形图”切换并点击“导出 PNG”，确认下载图片非空；点击 CSV/Excel，确认文件包含问题、SQL、数据源及结果。
3. 在同一会话追问“只看华东”，确认请求携带同一 `session_id` 且历史 SQL 可回放。
4. 将 `DROP TABLE`、多语句和写操作分别提交 `/api/query/execute-sql` 与 `/api/query/export`，确认均被拒绝且数据库未变化。
5. 给测试用户仅授权单表单字段且禁止导出，确认数据库列表、Schema、查询回放和导出均执行同一白名单策略。
6. 使用 Alice/Bob 分别创建会话，确认列表、详情、修改、清空和删除均无法跨用户访问；切换数据源后 Trace/Prompt 不再包含旧库 SQL。
7. 批量导入一条合法术语和一条非法术语，确认合法项以草稿导入、非法项返回逐条错误；导出 JSON/CSV 可重新读取。
8. 同步测试 SQLite 数据源，确认 Catalog 展示主键、外键和索引；修改表结构后再次同步，确认指纹变化且列出变更表。
9. 对带筛选条件的只读 SQL 请求执行计划，确认返回扫描步骤和人工索引候选；对写 SQL、未授权字段和其他用户隐藏的数据源确认拒绝。
10. 为 `orders` 配置 `region = '华东'` 行策略，确认直接查询、历史回放、JOIN/CTE、CSV/XLSX 导出和执行计划均应用过滤；策略文本不出现在普通查询响应 SQL 中。

## 当前不可宣称已完成的企业能力

公司 MySQL/PostgreSQL 接入、凭据引用、SSO/角色组同步、身份属性驱动的动态行策略、经过业务确认的指标口径、成熟时间序列库预测和正式业务准确率，仍需要真实数据源、身份系统与验收人员共同配置。本仓库已经把这些缺口显式化，避免用 SQLite 演示结果替代正式企业验收证据。
