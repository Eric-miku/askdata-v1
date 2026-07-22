# AskData 固定验收题集

`core-acceptance-scenarios.json` 固定计划书中的可量化门槛、多轮上下文、SQL 安全、Schema 指纹/变更检测和只读执行计划场景。它不依赖外部模型，可通过以下命令重复验证：

```bash
uv run pytest -q tests/test_acceptance_manifest.py
```

`bird-minidev-v4pro-seed42-100.json` 固定 NL2SQL 单轮问题 ID，用于连接指定模型后的 strict/relaxed 批量评测。

## BIRD Mini-Dev 基线

固定题集：`bird-minidev-v4pro-seed42-100.json`，模型：`deepseek-v4-pro`。

| 版本 | Relaxed EA | Strict EA | SQL 执行成功率 | 平均延迟 | P95 延迟 |
| --- | ---: | ---: | ---: | ---: | ---: |
| intern-agents 历史报告（2026-07-10） | 53% | 24% | 100% | 18,358.53 ms | 48,735.66 ms |
| askdata-v1 当前实现（2026-07-14） | 56% | 27% | 100% | 15,580.63 ms | 31,414.75 ms |

复测命令：

```bash
uv run askdata eval-bird \
  --model-name deepseek-v4-pro \
  --question-manifest benchmarks/bird-minidev-v4pro-seed42-100.json \
  --out reports/bird-eval-v4pro-100.json
```

完整报告保持 gitignored；报告会记录 manifest hash、processed 数据指纹、模型名、strict/relaxed EA、失败桶、重试率和延迟分位数。
