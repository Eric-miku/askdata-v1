# 数据处理

本目录包含 BIRD Mini-Dev 数据处理流水线。它把原始 BIRD 数据包转换为 AskData 后端、检索器、评测器和前端联调可直接使用的 SQLite 数据库与标准化 JSON/JSONL 文件。

## 当前结论

- 当前仅处理 BIRD Mini-Dev SQLite 数据集，不处理 Spider。
- 数据处理入口是 `./data-processing/askdata prepare-bird`。
- 数据处理交付的标准格式统一使用 `snake_case`。
- 可选传入 `--build-embeddings` 生成 Schema Embedding 向量索引，供后端检索器消费。
- BIRD 原始输入里仍会出现 `db_id`、`SQL` 等源数据字段；这些字段只存在于原始文件中，处理后会被标准化。
- 当前数据处理产物已经满足后端查询接口所需的 `database_id`、SQLite 路径、schema、问题和 Gold SQL 信息。
- 当前前端请求、后端 BIRD 数据读取、Retriever 和 Eval runner 已对齐标准数据处理产物；Schema Embedding 索引目前由数据处理侧生成，后端消费逻辑由后端检索器后续接入。

## 数据处理交付合同

下游对接时应优先使用下面这些标准产物和字段。

| 对接对象 | 使用产物 | 必要字段 / 路径 | 说明 |
|---|---|---|---|
| 数据库选择器 | `data/bird/databases/**/*.sqlite` 或 `data/bird/processed/databases.json` | `database_id`、`database_path` | 前端展示数据库列表时，ID 应使用 `database_id`。 |
| `/api/query` 请求 | 前端请求体 | `question`、`database_id`、可选 `session_id` | 不应发送 `database` 作为数据库字段；后端 Pydantic schema 要求 `database_id`。 |
| Retriever / Agent | `databases.json` + `schemas/{database_id}.json` + `schema_prompts/{database_id}.md` | `database_id`、`database_path`、`schema_path`、`schema_prompt_path` | 推荐后端用 `schema_path` 加载结构化 schema，或直接用 `schema_prompt_path` 作为 LLM schema 上下文。 |
| Schema Embedding Retriever | `vector_index/manifest.json` + `vector_index/schema_metadata.jsonl` + `vector_index/schema.faiss` | `id`、`database_id`、`doc_type`、`table_name`、`column_name`、`text` | 仅在 `--build-embeddings --vector-store faiss` 时生成；metadata 顺序与 FAISS 向量顺序一致。 |
| Eval runner | `questions.jsonl` + `gold_sql.jsonl` + `execution_cache.jsonl` | `question_id`、`database_id`、`question`、`gold_sql` | `execution_cache.jsonl` 是本地 Gold SQL 执行结果缓存，可用于验证数据集质量。 |
| Demo 子集 | `demo/demo_manifest.json` | `databases`、`question_ids`、分布统计 | 用于说明当前 Demo 构建范围和抽样结果。 |

## 当前代码对齐检查

| 模块 | 当前要求 | 数据处理产物 | 状态 |
|---|---|---|---|
| `/api/query` 后端 schema | `question`、`database_id`、可选 `session_id` | 标准问题和数据库 ID 均使用 `database_id` | 数据处理侧已对齐 |
| 当前前端查询 client | 发送 `database_id`、`question`、`session_id` | 数据处理产物使用 `database_id` | 已对齐 |
| 前端数据库选择器 | 通过 metadata API 加载 BIRD 数据库 ID | 产物包含 `financial`、`california_schools` 等 ID | 已对齐到后端 metadata API |
| 后端 metadata API | 扫描 `data/bird/databases/**/*.sqlite` | 处理脚本会复制 SQLite 到该目录 | 可用，但尚未读取 `processed/databases.json` |
| 当前后端 retriever | 通过 `LoadProcessedDatabases` 读取 `databases.json`，并按 `schema_path` 加载 `schemas/{database_id}.json` | 数据处理产物提供 `schema_path` 和完整结构化 schema | 已对齐 |
| 当前 Eval runner | 通过 `LoadProcessedQuestions` 优先读取 `questions.jsonl` | 数据处理标准产物是 `questions.jsonl`，字段是 `database_id`、`gold_sql` | 已对齐 |
| Schema Embedding 消费 | 后端检索器后续读取 `vector_index/` | 数据处理可选生成 `manifest.json`、`schema_metadata.jsonl` 和向量索引 | 数据处理侧已生成；后端消费逻辑不在本目录实现 |

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

## 从零复现 Schema Embedding + FAISS

如果目标是从下载后的 BIRD Mini-Dev 数据复现 schema embedding 索引，推荐使用下面这组命令。它只生成标准数据产物和 FAISS schema index，不执行 Gold SQL cache；`execution_cache.jsonl` 可在需要评测复现时单独补跑。

```bash
set -euo pipefail

# 1) 下载并规范化 BIRD Mini-Dev 原始数据
mkdir -p data/downloads data/bird/raw/minidev

uvx --from gdown gdown \
  "13VLWIwpw5E3d5DUkMvzw7hvHE67a4XkG" \
  -O data/downloads/bird_minidev.zip

python3 data-processing/scripts/prepare_raw_bird.py

# 2) 准备 FAISS 依赖；保证依赖安装在 uv run 使用的同一个环境里
uv sync
uv pip install faiss-cpu numpy

# 3) 使用已验证的内网 embedding 服务生成 schema FAISS 索引
EMBEDDING_API_URL="http://7.59.11.153:9106/v1/embeddings" \
EMBEDDING_MODEL_NAME="text-embedding" \
uv run python data-processing/src/askdata/cli.py prepare-bird \
  --raw-dir data/bird/raw/minidev \
  --db-dir data/bird/databases \
  --out-dir data/bird/processed \
  --demo-db-limit 11 \
  --demo-question-limit 500 \
  --build-embeddings \
  --embedding-provider openai-compatible \
  --embedding-api-base "$EMBEDDING_API_URL" \
  --embedding-model "$EMBEDDING_MODEL_NAME" \
  --embedding-batch-size 32 \
  --vector-store faiss \
  --force
```

复现成功后，核心产物应包括：

```text
data/bird/processed/databases.json
data/bird/processed/questions.jsonl
data/bird/processed/gold_sql.jsonl
data/bird/processed/schemas/*.json
data/bird/processed/schema_prompts/*.md
data/bird/processed/vector_index/manifest.json
data/bird/processed/vector_index/schema_metadata.jsonl
data/bird/processed/vector_index/schema.faiss
```

当前已验证结果为 11 个数据库、500 条 demo questions、873 条 schema documents、1024 维向量，其中 table document 75 条、column document 798 条。

如需同时生成 Gold SQL 执行缓存，可额外运行带 `--validate-sql --build-cache` 的 prepare 命令；该步骤用于 benchmark 复现和数据质量验证，不影响 schema embedding 索引生成。

## 输出产物

处理完成后应生成：

```text
data/bird/databases/
data/bird/processed/databases.json
data/bird/processed/schemas/{database_id}.json
data/bird/processed/schema_prompts/{database_id}.md
data/bird/processed/questions.jsonl
data/bird/processed/gold_sql.jsonl
data/bird/processed/execution_cache.jsonl                 # 可选，--validate-sql --build-cache
data/bird/processed/vector_index/manifest.json              # 可选
data/bird/processed/vector_index/schema_metadata.jsonl      # 可选
data/bird/processed/vector_index/schema.faiss               # 可选，FAISS 模式
data/bird/processed/vector_index/schema_vectors.jsonl       # 可选，JSONL 模式
data/bird/processed/demo/demo_manifest.json
data/bird/processed/preprocess_report.json
```

## Schema Embedding 索引

`prepare-bird` 默认不构建 embedding，避免每次预处理都调用模型接口。需要向量化所有 schema 表名、列名和描述时，增加 `--build-embeddings`：

当前内网 embedding 服务已验证可用：

```bash
EMBEDDING_API_URL="http://7.59.11.153:9106/v1/embeddings"
EMBEDDING_MODEL_NAME="text-embedding"
```

该服务返回 1024 维向量，单次 batch size 最大为 32。

```bash
./data-processing/askdata prepare-bird \
  --raw-dir data/bird/raw/minidev \
  --db-dir data/bird/databases \
  --out-dir data/bird/processed \
  --demo-db-limit 10 \
  --demo-question-limit 50 \
  --validate-sql \
  --build-cache \
  --build-embeddings \
  --embedding-provider openai-compatible \
  --embedding-api-base "$EMBEDDING_API_URL" \
  --embedding-model "$EMBEDDING_MODEL_NAME" \
  --embedding-batch-size 32 \
  --vector-store faiss \
  --force
```

`--embedding-api-base` 可以传 OpenAI-compatible base URL（如 `http://host:port/v1`），也可以直接传完整 embeddings endpoint（如 `http://host:port/v1/embeddings`）。环境变量优先读取 `EMBEDDING_API_URL`，其次读取 `EMBEDDING_API_BASE`。
默认 embedding batch size 是 32，以兼容当前内网 embedding 服务的批量限制。
如果 embedding 服务不要求鉴权，可以省略 `--embedding-api-key`；需要鉴权时再传 `--embedding-api-key "$EMBEDDING_API_KEY"`。

FAISS 模式需要本地安装可选依赖：

```bash
python3 -m pip install faiss-cpu numpy
```

用于无网络合同测试时，可用 deterministic hash embedding 和 JSONL vector store：

```bash
./data-processing/askdata prepare-bird \
  --raw-dir data/bird/raw/minidev \
  --db-dir data/bird/databases \
  --out-dir data/bird/processed \
  --build-embeddings \
  --embedding-provider hash \
  --embedding-model hash-test \
  --vector-store jsonl \
  --force
```

### `vector_index/manifest.json`

```json
{
  "version": 1,
  "source": "BIRD Mini-Dev SQLite",
  "index_type": "faiss",
  "embedding_provider": "openai-compatible",
  "embedding_model": "text-embedding",
  "dimension": 1024,
  "document_count": 873,
  "document_types": {"table": 75, "column": 798},
  "metadata_file": "schema_metadata.jsonl",
  "index_file": "schema.faiss",
  "vectors_file": null
}
```

### `vector_index/schema_metadata.jsonl`

每行对应向量索引中同位置的一条 schema document：

```json
{
  "id": "schema://financial/table/account/column/account_id",
  "database_id": "financial",
  "doc_type": "column",
  "table_name": "account",
  "column_name": "account_id",
  "data_type": "integer",
  "display_name": "account id",
  "is_primary_key": true,
  "row_count": null,
  "text": "Database: financial. Table: account. Column: account_id..."
}
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
import json
from pathlib import Path

required = [
    Path("data/bird/processed/databases.json"),
    Path("data/bird/processed/questions.jsonl"),
    Path("data/bird/processed/gold_sql.jsonl"),
    Path("data/bird/processed/preprocess_report.json"),
    Path("data/bird/processed/vector_index/manifest.json"),
    Path("data/bird/processed/vector_index/schema_metadata.jsonl"),
    Path("data/bird/processed/vector_index/schema.faiss"),
]
optional = [
    Path("data/bird/processed/execution_cache.jsonl"),
]

for path in required:
    print(("OK   " if path.exists() else "MISS ") + str(path))
for path in optional:
    print(("OK   " if path.exists() else "SKIP ") + str(path) + "  # optional: --validate-sql --build-cache")

questions = Path("data/bird/processed/questions.jsonl")
if questions.exists():
    print(f"questions: {sum(1 for _ in questions.open(encoding='utf-8'))}")

manifest = Path("data/bird/processed/vector_index/manifest.json")
metadata = Path("data/bird/processed/vector_index/schema_metadata.jsonl")
if manifest.exists() and metadata.exists():
    data = json.loads(manifest.read_text(encoding="utf-8"))
    metadata_count = sum(1 for _ in metadata.open(encoding="utf-8"))
    print(f"vector_index: {data['index_type']} {data['document_count']} docs x {data['dimension']} dims")
    print(f"metadata rows: {metadata_count}")
PY
```

## 质量标准

- `prepare-bird` 可重复运行，输出路径固定。
- 每条问题都有唯一 `question_id`、`database_id`、`question`、`gold_sql`。
- 每个数据库都有 `database_path`、`schema_path` 和完整 schema 文件。
- 开启 `--validate-sql` 后，Gold SQL 执行结果写入 `execution_cache.jsonl`。
- 所有新生成的 JSON / JSONL key 必须保持 `snake_case`。
