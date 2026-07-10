# 数据集预处理工程师规划方案

## 角色定位

数据集预处理工程师负责把原始 BIRD 数据集转化为系统可直接使用的标准化数据资产。

该岗位是后端、AI 检索、SQL 执行、自动评测之间的基础支撑角色，核心目标是保证 Demo 数据集 **可加载、可检索、可执行、可评测、可复现**。

## 核心职责

- 负责 `uv run askdata prepare-bird` 数据预处理命令的设计与实现。
- 解析原始数据集中的数据库文件、Schema、Question、Gold SQL 等信息。
- 生成统一的结构化 JSON / JSONL 配置文件，供后端、Agent、Retriever 和 Evaluator 使用。
- 验证 Gold SQL 在本地数据库中的可执行性，并缓存标准执行结果。
- 构建 V1 Demo 子集，控制数据库数量、问题数量和查询类型覆盖范围。
- 输出预处理报告，记录成功数量、失败样例、数据库路径、问题分布和 SQL 可执行率。

## 输入数据

- 原始数据目录：`data/bird/raw/`
- SQLite 数据库文件：`data/bird/databases/{database_id}/{database_id}.sqlite`
- 原始问题文件：包含自然语言问题、数据库 ID、标准 SQL、难度标签等字段
- 原始 Schema 信息：表名、字段名、字段类型、主键、外键、表字段注释等
- 项目配置文件：Demo 数据集规模、筛选规则、输出路径等

## 输出产物

- `data/bird/processed/databases.json`：数据库元信息列表
- `data/bird/processed/schemas/{database_id}.json`：单库结构化 Schema
- `data/bird/processed/questions.jsonl`：标准化自然语言问题集
- `data/bird/processed/gold_sql.jsonl`：标准 SQL 数据
- `data/bird/processed/schema_prompts/{database_id}.md`：供大模型使用的 Schema Prompt
- `data/bird/processed/demo/demo_manifest.json`：V1 Demo 子集配置
- `data/bird/processed/execution_cache.jsonl`：Gold SQL 标准执行结果缓存
- `data/bird/processed/preprocess_report.json`：预处理质量报告

## 标准数据格式

### Question / Gold SQL 标准格式

```json
{
  "question_id": "demo_001",
  "database_id": "sales_demo",
  "question": "查询每个地区的销售额排名",
  "gold_sql": "SELECT region, SUM(amount) FROM orders GROUP BY region ORDER BY SUM(amount) DESC LIMIT 10",
  "query_type": "group_by",
  "difficulty": "medium",
  "tables": ["orders"],
  "columns": ["region", "amount"]
}
```

### Schema 标准格式

```json
{
  "database_id": "sales_demo",
  "db_path": "data/bird/databases/sales_demo/sales_demo.sqlite",
  "tables": [
    {
      "table_name": "orders",
      "columns": [
        {
          "column_name": "order_id",
          "data_type": "INTEGER",
          "is_primary_key": true,
          "description": "订单ID"
        }
      ]
    }
  ],
  "foreign_keys": [
    {
      "source_table": "orders",
      "source_column": "customer_id",
      "target_table": "customers",
      "target_column": "customer_id"
    }
  ]
}
```

## CLI 命令规划

```bash
uv run askdata prepare-bird \
  --raw-dir data/bird/raw/minidev \
  --db-dir data/bird/databases \
  --out-dir data/bird/processed \
  --demo-db-limit 10 \
  --demo-question-limit 50
```

### 可选参数

```bash
--force                  # 覆盖已有处理结果
--split demo             # 指定处理 demo / dev / train
--validate-sql           # 执行 Gold SQL 校验
--build-cache            # 缓存标准 SQL 执行结果
--max-rows 200           # 限制缓存结果最大行数
```

## 阶段任务安排

| 阶段 | 任务 | 交付物 |
|---|---|---|
| 第 1 阶段：格式对齐 | 与架构师确认数据产物字段、路径规范、CLI 参数 | 数据格式说明文档 |
| 第 2 阶段：Schema 解析 | 读取 SQLite 元数据，提取表、字段、主键、外键 | `schemas/*.json` |
| 第 3 阶段：问题解析 | 标准化 question、database_id、gold_sql、difficulty | `questions.jsonl` |
| 第 4 阶段：SQL 验证 | 执行 Gold SQL，记录成功、失败、空结果 | `execution_cache.jsonl` |
| 第 5 阶段：Demo 构建 | 筛选 5–10 个库、30–50 条问题，覆盖核心查询类型 | `demo_manifest.json` |
| 第 6 阶段：报告输出 | 统计数据规模、查询类型、执行通过率、失败原因 | `preprocess_report.json` |

## Demo 子集筛选规则

- 优先选择 SQLite 可直接运行的数据库。
- 每个数据库至少包含 3–5 条可执行问题。
- 查询类型需覆盖：
  - 基础查询：`SELECT`
  - 条件过滤：`WHERE`
  - 聚合统计：`COUNT / SUM / AVG`
  - 分组分析：`GROUP BY`
  - 排序限制：`ORDER BY / LIMIT`
  - 多表关联：`JOIN`
  - 子查询：`Nested Query`
- 剔除无法执行、依赖特殊方言、结果为空且无法解释的问题。
- 保留少量中等复杂度 SQL，用于展示 Agent Repair 和评测能力。

## 与其他岗位的接口

- 向 **NL2SQL 与检索工程师** 提供 `schemas/*.json` 和 `schema_prompts/*.md`。
- 向 **数据库执行工程师** 提供标准数据库路径、Gold SQL 和执行缓存。
- 向 **系统评测工程师** 提供 `questions.jsonl`、`gold_sql.jsonl` 和 `execution_cache.jsonl`。
- 向 **前端工程师** 提供 Demo database 列表和预置 question 列表。
- 向 **架构师** 汇报数据格式变更，避免接口字段不一致。

## 质量验收标准

- `prepare-*` 命令可在 Mac / Windows / WSL 环境稳定运行。
- Demo 子集可重复生成，结果路径固定。
- 每条问题都有唯一 `question_id`、`database_id`、`question`、`gold_sql`。
- 每个 Demo 数据库都有完整 Schema 文件和数据库路径。
- Gold SQL 执行成功率达到 V1 设定阈值，建议不低于 90%。
- 预处理失败样例必须记录失败原因，不能静默跳过。
- 输出文件可被后端 API、Retriever、Evaluator 直接读取。

## 主要风险与应对

| 风险 | 应对策略 |
|---|---|
| 数据集路径不统一 | 通过配置文件和 CLI 参数统一输入输出路径 |
| SQL 方言不兼容 | 优先筛选 SQLite 可执行样例，复杂方言样例放入后续版本 |
| Schema 信息缺失 | 从 SQLite 自动反查表结构，必要时用字段名作为默认描述 |
| Gold SQL 执行失败 | 记录错误类型，区分语法错误、表缺失、字段缺失、空结果 |
| Demo 覆盖不足 | 按查询类型分桶抽样，避免 Demo 只覆盖简单查询 |

## 最终交付清单

- `prepare-bird` 预处理命令
- 标准化 Schema 文件
- 标准化 Question / Gold SQL 文件
- Demo 子集配置文件
- Gold SQL 执行结果缓存
- 预处理质量报告
- 数据目录说明文档
- 给后端、AI、评测模块使用的数据格式说明

## 一句话概括

数据集预处理工程师的任务不是简单搬运数据，而是把原始 Benchmark 数据集加工成整个智能问数系统可稳定调用的“标准数据底座”。
