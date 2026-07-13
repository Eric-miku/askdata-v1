# 数据处理

本目录包含 BIRD Mini-Dev 数据处理流水线。它把原始 BIRD 数据包转换为 AskData 后端、检索器、评测器和前端联调可直接使用的 SQLite 数据库与标准化 JSON/JSONL 文件。

## 当前结论

- 当前仅处理 BIRD Mini-Dev SQLite 数据集，不处理 Spider。
- 数据处理入口是 `./data-processing/askdata prepare-bird`。
- 数据处理交付的标准格式统一使用 `snake_case`。
- BIRD 原始输入里仍会出现 `db_id`、`SQL` 等源数据字段；这些字段只存在于原始文件中，处理后会被标准化。
- 当前数据处理产物已经满足后端查询接口所需的 `database_id`、SQLite 路径、schema、问题和 Gold SQL 信息。
- 当前项目代码中仍有少量历史格式依赖，需要对接方适配，或由数据处理侧后续补充兼容产物；详见下方“当前代码对齐检查”。

## 数据处理交付合同

下游对接时应优先使用下面这些标准产物和字段。

| 对接对象 | 使用产物 | 必要字段 / 路径 | 说明 |
|---|---|---|---|
| 数据库选择器 | `data/bird/databases/**/*.sqlite` 或 `data/bird/processed/databases.json` | `database_id`、`database_path` | 前端展示数据库列表时，ID 应使用 `database_id`。 |
| `/api/query` 请求 | 前端请求体 | `question`、`database_id`、可选 `session_id` | 不应发送 `database` 作为数据库字段；后端 Pydantic schema 要求 `database_id`。 |
| Retriever / Agent | `databases.json` + `schemas/{database_id}.json` + `schema_prompts/{database_id}.md` | `database_id`、`database_path`、`schema_path`、`schema_prompt_path` | 推荐后端用 `schema_path` 加载结构化 schema，或直接用 `schema_prompt_path` 作为 LLM schema 上下文。 |
| Eval runner | `questions.jsonl` + `gold_sql.jsonl` + `execution_cache.jsonl` | `question_id`、`database_id`、`question`、`gold_sql` | `execution_cache.jsonl` 是本地 Gold SQL 执行结果缓存，可用于验证数据集质量。 |
| Demo 子集 | `demo/demo_manifest.json` | `databases`、`question_ids`、分布统计 | 用于说明当前 Demo 构建范围和抽样结果。 |

## 当前代码对齐检查

| 模块 | 当前要求 | 数据处理产物 | 状态 |
|---|---|---|---|
| `/api/query` 后端 schema | `question`、`database_id`、可选 `session_id` | 标准问题和数据库 ID 均使用 `database_id` | 数据处理侧已对齐 |
| 当前前端查询 client | 发送 `database` 和 `question` | 数据处理产物使用 `database_id` | 未完全对齐；前端需要把 `database` 改为 `database_id` |
| 前端数据库选择器 | 应加载 BIRD 数据库 ID | 产物包含 `financial`、`california_schools` 等 ID | 数据源已准备；前端还需接 metadata API |
| 后端 metadata API | 扫描 `data/bird/databases/**/*.sqlite` | 处理脚本会复制 SQLite 到该目录 | 可用，但尚未读取 `processed/databases.json` |
| 当前后端 retriever | 读取 `databases.json`，当前实现主要依赖每个 database 内联的 `tables` / `foreignKeys` | 数据处理产物把完整 schema 写在 `schemas/{database_id}.json`，并在 `databases.json` 提供 `schema_path` | 未完全对齐；后端应按 `schema_path` 加载 schema，或数据处理侧补充兼容内联 schema |
| 当前 Eval runner | 读取 `questions.json`，并优先找 `databaseId` / `db_id`、`goldSql` / `SQL` | 数据处理标准产物是 `questions.jsonl`，字段是 `database_id`、`gold_sql` | 未完全对齐；Eval runner 应改读 JSONL，或数据处理侧补充兼容 `questions.json` |

## 数据目录

| 路径 | 说明 |
|---|---|
| `data/downloads/bird_minidev.zip` | BIRD Mini-Dev 原始压缩包 |
| `data/bird/raw/minidev/` | 解压并归一化后的 BIRD Mini-Dev 原始目录 |
| `data/bird/raw/minidev/MINIDEV/dev_tables.json` | BIRD 原始 schema 元数据 |
| `data/bird/raw/minidev/MINIDEV/mini_dev_sqlite.json` | 原始自然语言问题和 Gold SQL |
| `data/bird/raw/minidev/MINIDEV/dev_databases/` | 原始 SQLite 数据库目录 |
| `data/bird/databases/` | 处理后复制出的标准 SQLite 数据库目录 |
| `data/bird/processed/` | 标准化输出目录 |

## 一键下载并处理

从仓库根目录运行下面整段命令：

```bash
set -euo pipefail

mkdir -p data/downloads data/bird/raw/minidev

uvx --from gdown gdown \
  "13VLWIwpw5E3d5DUkMvzw7hvHE67a4XkG" \
  -O data/downloads/bird_minidev.zip

python3 data-processing/scripts/prepare_raw_bird.py

./data-processing/askdata prepare-bird \
  --raw-dir data/bird/raw/minidev \
  --db-dir data/bird/databases \
  --out-dir data/bird/processed \
  --demo-db-limit 10 \
  --demo-question-limit 50 \
  --validate-sql \
  --build-cache \
  --force
```

如果当前环境没有 `uvx`，可以先用 Python 安装 `gdown`，再重新执行下载步骤：

```bash
python3 -m pip install --user gdown
python3 -m gdown \
  "13VLWIwpw5E3d5DUkMvzw7hvHE67a4XkG" \
  -O data/downloads/bird_minidev.zip
```

## 输出产物

处理完成后应生成：

```text
data/bird/databases/
data/bird/processed/databases.json
data/bird/processed/schemas/{database_id}.json
data/bird/processed/schema_prompts/{database_id}.md
data/bird/processed/questions.jsonl
data/bird/processed/gold_sql.jsonl
data/bird/processed/execution_cache.jsonl
data/bird/processed/demo/demo_manifest.json
data/bird/processed/preprocess_report.json
```

### `databases.json`

列表格式。每个元素代表一个可用数据库：

```json
{
  "database_id": "financial",
  "database_path": "data/bird/databases/financial/financial.sqlite",
  "schema_path": "data/bird/processed/schemas/financial.json",
  "schema_prompt_path": "data/bird/processed/schema_prompts/financial.md",
  "table_count": 8,
  "column_count": 64,
  "question_count": 42,
  "executable_question_count": 5
}
```

### `schemas/{database_id}.json`

单库结构化 schema：

```json
{
  "database_id": "financial",
  "database_path": "data/bird/databases/financial/financial.sqlite",
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

### `questions.jsonl`

JSONL 格式，每行一个问题：

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

### `gold_sql.jsonl`

```json
{
  "question_id": "bird_1471",
  "database_id": "debit_card_specializing",
  "gold_sql": "SELECT ..."
}
```

### `execution_cache.jsonl`

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
  "latency_ms": 12.3
}
```

## 快速校验

```bash
python3 - <<'PY'
from pathlib import Path

required = [
    Path("data/bird/processed/databases.json"),
    Path("data/bird/processed/questions.jsonl"),
    Path("data/bird/processed/gold_sql.jsonl"),
    Path("data/bird/processed/execution_cache.jsonl"),
    Path("data/bird/processed/preprocess_report.json"),
]

for path in required:
    print(("OK   " if path.exists() else "MISS ") + str(path))

questions = Path("data/bird/processed/questions.jsonl")
if questions.exists():
    print(f"questions: {sum(1 for _ in questions.open(encoding='utf-8'))}")
PY
```

## 质量标准

- `prepare-bird` 可重复运行，输出路径固定。
- 每条问题都有唯一 `question_id`、`database_id`、`question`、`gold_sql`。
- 每个数据库都有 `database_path`、`schema_path` 和完整 schema 文件。
- 开启 `--validate-sql` 后，Gold SQL 执行结果写入 `execution_cache.jsonl`。
- 所有新生成的 JSON / JSONL key 必须保持 `snake_case`。
