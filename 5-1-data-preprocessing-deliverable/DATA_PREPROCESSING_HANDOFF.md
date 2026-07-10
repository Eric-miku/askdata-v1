# 数据集预处理对接 README

本文档面向架构、AI / Agent、后端执行、评测测试、前端展示等其他成员，说明 **数据集预处理工程师** 当前已完成的 BIRD Mini-Dev 数据底座、产物位置、字段契约和对接方式。

## 1. 当前结论

当前实现符合 `dataset-preprocessing-engineer-plan.md` 中数据集预处理工程师的核心任务要求：

- 已实现 `uv run askdata prepare-bird` 预处理命令。
- 已完成 BIRD Mini-Dev SQLite 数据集解析。
- 已生成标准化 Schema、Question、Gold SQL、Schema Prompt、Demo Manifest、Execution Cache 和预处理报告。
- 已验证 Gold SQL 本地可执行性。
- 当前仅使用 BIRD 数据集，Spider 相关目录已移除。

## 2. 快速运行

推荐命令：

```bash
uv run askdata prepare-bird \
  --raw-dir data/bird/raw/minidev \
  --db-dir data/bird/databases \
  --out-dir data/bird/processed \
  --demo-db-limit 10 \
  --demo-question-limit 50 \
  --validate-sql \
  --build-cache \
  --force
```

离线或临时环境兜底命令：

```bash
./askdata prepare-bird \
  --raw-dir data/bird/raw/minidev \
  --db-dir data/bird/databases \
  --out-dir data/bird/processed \
  --demo-db-limit 10 \
  --demo-question-limit 50 \
  --validate-sql \
  --build-cache \
  --force
```

## 3. 数据目录

| 路径 | 说明 | 使用方 |
|---|---|---|
| `data/downloads/bird_minidev.zip` | BIRD Mini-Dev 原始压缩包 | 数据组 |
| `data/bird/raw/minidev/` | 解压后的 BIRD Mini-Dev 原始目录 | 数据组 |
| `data/bird/raw/minidev/MINIDEV/dev_tables.json` | BIRD 原始 Schema 元数据 | 数据组 / AI 组 |
| `data/bird/raw/minidev/MINIDEV/mini_dev_sqlite.json` | 原始自然语言问题和 Gold SQL | 数据组 / 评测组 |
| `data/bird/raw/minidev/MINIDEV/dev_databases/` | 原始 SQLite 数据库目录 | 数据组 |
| `data/bird/databases/` | 预处理后复制出的标准数据库目录 | 后端执行组 |
| `data/bird/processed/` | 标准化输出目录 | 全组 |

## 4. 输出产物

| 产物 | 说明 | 主要使用方 |
|---|---|---|
| `data/bird/processed/databases.json` | 数据库列表、数据库路径、Schema 路径、问题数量 | 后端 API、数据库执行、前端 |
| `data/bird/processed/schemas/{database_id}.json` | 单库结构化 Schema，含表、列、主键、外键、行数 | AI / NL2SQL、检索、后端 |
| `data/bird/processed/schema_prompts/{database_id}.md` | 可直接注入 LLM Prompt 的 Schema 文本 | AI / NL2SQL、Agent |
| `data/bird/processed/questions.jsonl` | Demo 自然语言问题集，含 Gold SQL 和查询特征 | 前端、API、AI、评测 |
| `data/bird/processed/gold_sql.jsonl` | `question_id` 到 Gold SQL 的标准答案映射 | 评测组 |
| `data/bird/processed/execution_cache.jsonl` | Gold SQL 执行结果缓存，含 columns / rows / success | 评测组、前端 Mock、后端联调 |
| `data/bird/processed/demo/demo_manifest.json` | V1 Demo 子集清单，含数据库和问题分布 | 架构、前端、API |
| `data/bird/processed/preprocess_report.json` | 预处理质量报告，含成功率、失败样例和输出路径 | 架构、测试 |

## 5. 当前验证结果

最后一次验证命令已通过：

```bash
uv run askdata prepare-bird \
  --raw-dir data/bird/raw/minidev \
  --db-dir data/bird/databases \
  --out-dir data/bird/processed \
  --demo-db-limit 10 \
  --demo-question-limit 50 \
  --validate-sql \
  --build-cache \
  --force
```

验证结果：

| 指标 | 当前结果 |
|---|---:|
| 原始问题数 | 500 |
| Demo 数据库数 | 10 |
| Demo 问题数 | 50 |
| SQLite 数据库文件数 | 11 |
| Schema JSON 文件数 | 11 |
| Schema Prompt 文件数 | 11 |
| Gold SQL 验证数 | 50 |
| Gold SQL 执行成功数 | 50 |
| Gold SQL 执行失败数 | 0 |
| 执行成功率 | 100% |

Demo 查询覆盖：

- `SELECT`
- `WHERE`
- 聚合：`COUNT / SUM / AVG / MIN / MAX`
- `GROUP BY`
- `ORDER BY / LIMIT`
- 多表 `JOIN`
- Nested Query

当前 Demo 数据库：

- `california_schools`
- `card_games`
- `codebase_community`
- `debit_card_specializing`
- `european_football_2`
- `financial`
- `formula_1`
- `student_club`
- `superhero`
- `thrombosis_prediction`

## 6. 字段契约

### 6.1 `databases.json`

列表格式。每个元素代表一个可用数据库。

关键字段：

```json
{
  "database_id": "financial",
  "db_path": "data/bird/databases/financial/financial.sqlite",
  "schema_path": "data/bird/processed/schemas/financial.json",
  "schema_prompt_path": "data/bird/processed/schema_prompts/financial.md",
  "table_count": 8,
  "column_count": 64,
  "question_count": 42,
  "executable_question_count": 5
}
```

对接建议：

- 后端 metadata 接口可直接读取该文件生成 database selector。
- SQL 执行模块用 `db_path` 建立 SQLite 连接。
- AI 检索模块用 `schema_path` 或 `schema_prompt_path` 构造上下文。

### 6.2 `schemas/{database_id}.json`

单库 Schema 文件。

关键字段：

```json
{
  "database_id": "financial",
  "db_path": "data/bird/databases/financial/financial.sqlite",
  "table_count": 8,
  "column_count": 64,
  "question_count": 42,
  "tables": [
    {
      "table_name": "account",
      "display_name": "account",
      "row_count": 4500,
      "columns": [
        {
          "column_name": "account_id",
          "display_name": "account id",
          "data_type": "integer",
          "is_primary_key": true,
          "description": "account id"
        }
      ]
    }
  ],
  "foreign_keys": [
    {
      "source_table": "loan",
      "source_column": "account_id",
      "target_table": "account",
      "target_column": "account_id"
    }
  ]
}
```

对接建议：

- NL2SQL 组可按 `tables[].columns[]` 组织 Schema Linking。
- 后端可用 `tables` 和 `foreign_keys` 提供 `/api/metadata/tables?database_id=`。
- 若后续做业务术语库，可从 `table_name`、`display_name`、`column_name`、`description` 生成术语种子。

### 6.3 `questions.jsonl`

JSONL 格式，每行一个问题。

关键字段：

```json
{
  "question_id": "bird_1471",
  "source_question_id": 1471,
  "database_id": "debit_card_specializing",
  "question": "What is the ratio of customers who pay in EUR against customers who pay in CZK?",
  "evidence": "ratio = count(EUR) / count(CZK)",
  "gold_sql": "SELECT ...",
  "query_type": "aggregation",
  "query_features": ["select", "aggregation"],
  "difficulty": "simple",
  "tables": ["customers"],
  "columns": ["Currency"]
}
```

对接建议：

- 前端问题选择器读取 `question_id`、`database_id`、`question`。
- `/api/query` 联调时可以用 `question` 作为输入，`database_id` 作为目标库。
- AI 组可以用 `gold_sql` 做 few-shot 或 prompt 调试参考。
- 评测组用 `question_id` 对齐预测 SQL 和 Gold SQL。

### 6.4 `gold_sql.jsonl`

JSONL 格式，每行一个 Gold SQL。

关键字段：

```json
{
  "question_id": "bird_1471",
  "database_id": "debit_card_specializing",
  "gold_sql": "SELECT ..."
}
```

对接建议：

- 评测组使用该文件计算 Exact Match。
- 执行准确率评测应以 `question_id` 关联 `execution_cache.jsonl`。

### 6.5 `execution_cache.jsonl`

JSONL 格式，每行一个 Gold SQL 执行结果。

关键字段：

```json
{
  "question_id": "bird_1471",
  "database_id": "debit_card_specializing",
  "gold_sql": "SELECT ...",
  "success": true,
  "columns": ["ratio"],
  "rows": [[1.23]],
  "row_count": 1,
  "error": null,
  "latency_ms": 1.48
}
```

对接建议：

- 评测组用 `rows` 计算 Execution Accuracy。
- 前端可用 `columns` / `rows` 做表格和图表 Mock。
- 如果 `success=false`，错误信息会写入 `error`，当前 Demo 子集没有失败样例。

### 6.6 `demo/demo_manifest.json`

Demo 子集总清单。

关键字段：

```json
{
  "name": "bird_minidev_demo",
  "source": "BIRD Mini-Dev SQLite",
  "database_count": 10,
  "question_count": 50,
  "databases": [],
  "question_ids": [],
  "query_type_distribution": {},
  "query_feature_distribution": {}
}
```

对接建议：

- 架构师可用它确认 V1 demo 范围。
- 前端可用 `databases` 渲染 database selector。
- 前端或 API 可用 `question_ids` 从 `questions.jsonl` 过滤预置问题。

## 7. 各岗位对接方式

### 7.1 架构师 / 组长

读取：

- `data/bird/processed/preprocess_report.json`
- `data/bird/processed/demo/demo_manifest.json`

用途：

- 确认 V1 数据范围。
- 检查 SQL 执行成功率。
- 对齐前后端 API 契约里的 `database_id`、`question_id`、`columns`、`rows`。

### 7.2 AI 与 Agent 逻辑组

读取：

- `data/bird/processed/schema_prompts/{database_id}.md`
- `data/bird/processed/schemas/{database_id}.json`
- `data/bird/processed/questions.jsonl`

用途：

- `schema_prompts` 可直接注入 LLM Prompt。
- `schemas` 用于 Schema Linking / SemanticRetriever。
- `questions.jsonl` 中的 `gold_sql` 可做 prompt 调试、few-shot 样例和错误分析。

推荐流程：

```text
用户问题 + database_id
→ 根据 database_id 读取 schema_prompt
→ LLM 生成 SQL
→ sqlglot 校验
→ SQLAlchemy 执行
```

### 7.3 后端 API 与会话开发组

读取：

- `data/bird/processed/databases.json`
- `data/bird/processed/questions.jsonl`
- `data/bird/processed/demo/demo_manifest.json`

可支撑接口：

- `GET /api/metadata/databases`
- `GET /api/metadata/tables?database_id=`
- `GET /api/spider/questions` 或后续改名为 `GET /api/bird/questions`
- `POST /api/query`

注意：

- 当前数据源是 BIRD，不建议接口继续强绑定 `spider` 命名。
- 若接口名暂时沿用 `/api/spider/questions`，建议内部字段仍使用通用的 `database_id`、`question_id`。

### 7.4 数据库引擎与执行组

读取：

- `data/bird/processed/databases.json`
- `data/bird/databases/{database_id}/{database_id}.sqlite`
- `data/bird/processed/execution_cache.jsonl`

用途：

- 用 `databases.json` 找到目标 SQLite 路径。
- 用 `execution_cache.jsonl` 对比执行结果格式。
- SQL 执行结果建议统一返回：

```json
{
  "columns": ["col_a", "col_b"],
  "rows": [[1, "x"], [2, "y"]]
}
```

### 7.5 前端交互与可视化组

读取：

- `data/bird/processed/demo/demo_manifest.json`
- `data/bird/processed/questions.jsonl`
- `data/bird/processed/execution_cache.jsonl`

用途：

- database selector：读取 `demo_manifest.databases`。
- question selector：按 `question_ids` 匹配 `questions.jsonl`。
- Mock 表格：读取 `execution_cache.columns` 和 `execution_cache.rows`。
- 图表 Mock：可用 `columns` / `rows` 交给 ECharts 规则引擎。

### 7.6 系统评测与测试组

读取：

- `data/bird/processed/questions.jsonl`
- `data/bird/processed/gold_sql.jsonl`
- `data/bird/processed/execution_cache.jsonl`
- `data/bird/processed/preprocess_report.json`

用途：

- Exact Match：预测 SQL 与 `gold_sql.jsonl` 对比。
- Execution Accuracy：预测 SQL 执行结果与 `execution_cache.jsonl` 对比。
- 集成测试：确认 `/api/query` 对 50 条 Demo 问题能稳定返回。

## 8. 完成度审计

| 规划要求 | 当前状态 | 证据 |
|---|---|---|
| `prepare-bird` 命令 | 已完成 | `uv run askdata prepare-bird ...` 成功执行 |
| 解析 Schema | 已完成 | `processed/schemas/*.json` 共 11 个 |
| 生成 Schema Prompt | 已完成 | `processed/schema_prompts/*.md` 共 11 个 |
| 标准化 Question | 已完成 | `questions.jsonl` 共 50 行 |
| 标准化 Gold SQL | 已完成 | `gold_sql.jsonl` 共 50 行 |
| SQL 可执行性验证 | 已完成 | `execution_cache.jsonl` 共 50 行，成功率 100% |
| Demo 子集 5–10 个库 | 已完成 | `demo_manifest.database_count = 10` |
| Demo 子集 30–50 条问题 | 已完成 | `demo_manifest.question_count = 50` |
| 查询类型覆盖 | 已完成 | 覆盖 SELECT、WHERE、聚合、GROUP BY、ORDER BY / LIMIT、JOIN、Nested Query |
| 预处理报告 | 已完成 | `preprocess_report.json` |
| 数据目录说明 | 已完成 | `data/bird/README.md` 和本文档 |

## 9. 注意事项

- `data/` 目录包含较大数据文件，通常不应提交到 Git。
- 当前只处理 BIRD Mini-Dev SQLite，不处理 Spider。
- 当前 Gold SQL 验证基于 SQLite 方言。
- `uv.toml` 已配置项目内缓存目录 `.uv-cache`，避免默认用户缓存目录不可写。
- `./askdata` 是本地兜底入口，正常协作优先使用 `uv run askdata prepare-bird`。
