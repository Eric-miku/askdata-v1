# AskData 后端 API 接口文档

## 概述

基于 FastAPI 构建的 Text-to-SQL 智能问数平台后端服务。所有路由统一挂载在 `/api` 前缀下。

---

## 1. `GET /api/metadata/databases` — 获取数据库列表

### 输入
无请求参数。

### 输出格式
```json
[
    {
        "id": "california_schools",
        "name": "California Schools",
        "path": "/path/to/bird/data/databases/california_schools.db",
        "tables_count": 8
    },
    {
        "id": "debit_card_specializing",
        "name": "Debit Card Specializing",
        "path": "/path/to/bird/data/databases/debit_card_specializing.db",
        "tables_count": 5
    }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `string` | 数据库标识符（文件名不含扩展名） |
| `name` | `string` | 可读名称（下划线转空格、首字母大写） |
| `path` | `string` | 数据库文件绝对路径 |
| `tables_count` | `integer` | 该数据库中表的数量（实时读取 SQLite） |

---

## 2. `GET /api/metadata/{database_id}/tables` — 获取表结构

### 输入
| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `database_id` | URL 路径 | `string` | 是 | 数据库 ID，如 `california_schools` |

### 输出格式
```json
{
    "database_id": "california_schools",
    "tables": [
        {
            "table_name": "schools",
            "columns": [
                {"name": "School", "type": "TEXT", "primary_key": false, "nullable": true},
                {"name": "Total_Students", "type": "INTEGER", "primary_key": false, "nullable": true}
            ]
        }
    ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `database_id` | `string` | 数据库 ID |
| `tables[].table_name` | `string` | 表名 |
| `tables[].columns[].name` | `string` | 字段名 |
| `tables[].columns[].type` | `string` | 字段类型（SQLite 类型，缺失时默认为 `TEXT`） |
| `tables[].columns[].primary_key` | `boolean` | 是否为主键 |
| `tables[].columns[].nullable` | `boolean` | 是否允许为空 |

### 错误响应
- `404`: 数据库未找到
- `500`: 读取数据库结构失败

---

## 3. `POST /api/sessions` — 创建对话会话

### 输入 — 请求体 (JSON)
```json
{
    "database_id": "california_schools"
}
```

| 字段 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `database_id` | Body | `string` | 否 | 可选的默认关联数据库 ID |

### 输出格式
```json
{
    "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "created_at": 1704067200.0
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | `string` | UUID v4 格式的会话唯一标识 |
| `created_at` | `float` | 创建时的 Unix 时间戳（秒） |

---

## 4. `DELETE /api/sessions/{session_id}` — 删除对话会话

### 输入
| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `session_id` | URL 路径 | `string` | 是 | 要删除的会话 ID |

### 输出格式
```json
{
    "success": true,
    "message": "会话已删除"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | `boolean` | 是否成功删除 |
| `message` | `string` | 操作结果描述 |

### 错误响应
- `404`: 会话未找到

---

## 5. `POST /api/query` — 自然语言查询（核心接口）

### 输入 — 请求体 (JSON)

**对应的 Pydantic 模型：`QueryRequest`**

```json
{
    "question": "加州哪个学校的学生最多？",
    "database_id": "california_schools",
    "session_id": "a1b2c3d4-..."
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | `string` | **是** | 用户输入的自然语言问题 |
| `database_id` | `string` | **是** | 选中的 BIRD 数据库 ID |
| `session_id` | `string` | 否 | 多轮对话的会话 ID（不传则自动创建新会话） |

### 输出格式

**对应的 Pydantic 模型：`QueryResponse`**

```json
{
    "answer": "加州学生人数最多的学校是 ABC High School，共有 2,450 名学生。",
    "sql": "SELECT School, Total_Students FROM schools ORDER BY Total_Students DESC LIMIT 1",
    "columns": ["School", "Total_Students"],
    "rows": [
        {"School": "ABC High School", "Total_Students": 2450}
    ],
    "chart": {
        "type": "bar",
        "xAxis": {"data": ["ABC High School"]},
        "yAxis": {"name": "学生数"},
        "series": [{"data": [2450]}]
    },
    "trace": [
        {"step": "收到查询请求", "status": "info", "message": "[abc123][+0.00s] 收到查询请求: question='加州哪个学校的学生最多？', database_id='california_schools'"},
        {"step": "创建新会话", "status": "info", "message": "[abc123][+0.01s] 创建新会话: session_id=..."},
        {"step": "调用 Agent", "status": "info", "message": "..."}
    ],
    "error": null
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `answer` | `string` | LLM 生成的最终中文解释（必含） |
| `sql` | `string` (nullable) | 系统生成并执行的 SQL 语句 |
| `columns` | `string[]` (nullable) | 表格列名列表 |
| `rows` | `object[]` (nullable) | 表格数据行，每行为 `column → value` 字典 |
| `chart` | `object` (nullable) | ECharts 图表配置对象，含 `type`, `xAxis`, `yAxis`, `series` 等 |
| `trace` | `object[]` | Agent 执行全链路的轨迹日志列表（API 层 + Agent 层合并） |
| `error` | `string` (nullable) | 执行过程中的错误信息（成功时为 `null`） |

> **注意：** 接口内部会先处理会话逻辑（查找/创建会话、更新历史），然后调用 `AgentGraph` 工作流执行 NL2SQL 全链路（语义检索 → LLM 生成 SQL/ReAct 循环 → SQL 执行 → 结果分析），最后将结果保存到会话历史并返回。

---

## 附录：内部数据模型

### SessionManager 会话存储结构（内存 Dict）

```python
{
    "session_id": "uuid-string",       # UUID 会话标识
    "created_at": 1704067200.0,        # Unix 时间戳
    "database_id": "california_schools",  # 关联的数据库 ID
    "history": [
        {
            "question": "用户问题",
            "sql": "SELECT ...",        # 生成的 SQL
            "answer": "中文回答",       # LLM 回答
            "timestamp": 1704067200.0   # 时间戳
        }
    ]
}
```

### TraceLogger 日志条目格式

```python
{
    "step": "步骤名称",      # 如 "收到查询请求", "调用 LLM 生成 SQL"
    "status": "info|error",  # 状态标识
    "message": "[trace_id][+耗时] 步骤名: 详情"  # 完整消息（含 Trace ID 和相对耗时）
}
```

### 异常响应格式

当接口出错时，返回 FastAPI 默认错误结构：
```json
{
    "detail": "错误描述信息"
}
```

可能的状态码：`404`（资源未找到）、`500`（服务器内部错误）。
