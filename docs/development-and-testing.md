# AskData 开发与测试指南

## 环境准备

需要 Python 3.10+、Node.js/npm 和可访问的 OpenAI-compatible 模型。推荐从仓库根目录执行：

```bash
bash scripts/setup-dev-env.sh
uv run askdata --help
cd frontend && npm install
```

复制 `.env.example` 为 `.env`，配置模型地址、模型名和 BIRD 数据目录。不得将真实令牌、账号、数据库 URL 或公司地址提交到仓库。

## 本地开发

```bash
uv run askdata serve
cd frontend && npm run dev
```

后端默认 `http://localhost:8000`，前端默认 `http://localhost:5173`。Vite 将 `/api` 代理到后端。

## 模块责任

| 目录 | 责任 |
| --- | --- |
| `backend/askdata/agent` | 结构化理解、Prompt、Agent 编排和 SQL 修复 |
| `backend/askdata/tools` | Schema 检索、图表、结构化分析和导出 |
| `backend/askdata/db` | AST 校验、执行、超时、结果治理和执行计划 |
| `backend/askdata/data` | BIRD 适配、数据源生命周期和 Schema Catalog |
| `backend/askdata/security` | 用户对象权限和执行上下文授权 |
| `backend/askdata/knowledge` | 术语、指标、映射和版本存储 |
| `backend/askdata/api` | HTTP 契约、会话、审计和安全异常 |
| `frontend/src` | 用户工作区、管理页面、状态、API 客户端和图表渲染 |
| `data-processing` | 唯一的 BIRD 数据生产者，后端不得复制其准备逻辑 |

后端 NL2SQL 面向方法沿用 `Build`、`Retrieve`、`Run` 等 PascalCase；数据库基础模块使用现有 snake_case。新增代码应优先复用当前存储、权限和路由模式。

## 自动化测试

发布前最小检查：

```bash
uv run pytest -q
cd frontend && npm test -- --run
cd frontend && npx tsc --noEmit
cd frontend && npm run build
git diff --check
```

测试分层：

| 层级 | 证据 |
| --- | --- |
| 单元 | 理解合并、SQL AST、图表规则、分析计算、Catalog 指纹、导出编码 |
| 存储 | 会话迁移/隔离、术语版本、数据源同步、对象与行级权限策略 |
| API 集成 | 元数据过滤、查询/回放/导出、行过滤、管理令牌、安全异常、执行计划 |
| 前端组件 | 加载/空/错误、历史恢复、图表切换、Catalog、执行计划 |
| 固定验收 | `tests/test_acceptance_manifest.py` 驱动机器可读多轮、安全和治理场景 |
| 模型评测 | 固定 BIRD manifest，成对报告 strict/relaxed EA、执行率和延迟 |

涉及共享安全入口时，测试必须同时覆盖查询、历史回放、导出和执行计划，不能只验证单一路由。行过滤测试还必须覆盖 JOIN、CTE、别名、策略语法拒绝和原 SQL 不泄露内部条件。

## BIRD 与业务评测

```bash
uv run askdata eval-bird \
  --model-name deepseek-v4-pro \
  --question-manifest benchmarks/bird-minidev-v4pro-seed42-100.json \
  --out reports/bird-eval-v4pro-100.json
```

每次可比较评测必须记录模型名、manifest hash、processed 数据指纹、strict/relaxed EA、SQL 执行率、失败桶、重试率、平均/P95 延迟。不得通过放宽 comparer 提高 relaxed 指标。

公司正式验收还需要版本固定的真实业务题集、期望结果/SQL、指标口径负责人和签字记录。

## 测试数据和隔离

- 测试状态使用独立 `ASKDATA_STATE_DIR`，避免污染 `.checkpoints`。
- SQLite 测试库在 `tmp_path` 创建；业务数据文件不得写入测试结果。
- 后端测试建议设置 `PYTHONDONTWRITEBYTECODE=1`，兼容只读或同步挂载目录。
- 前端测试不得依赖外部模型或真实后端。
- 日志断言只检查哈希和关联 ID，不输出 SQL 参数、凭据或结果明文。

## 完成定义

一项变更只有在以下条件满足后才可进入评审：

- 成功、失败和边界路径都有与风险相称的测试。
- API、前端类型、用户交互和持久化迁移保持一致。
- 权限、安全、日志和历史重放入口已检查。
- 配置示例和相关文档已更新。
- 全量后端、前端、类型和构建检查通过。
- 固定回归指标未下降；若依赖外部环境，缺少的证据被明确记录。
