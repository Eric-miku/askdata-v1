# 可复现评测

## V2.1 演示回归套件

版本化的演示题和离线预测保存在 `tests/fixtures/v2_demo_cases.json`。运行：

```bash
uv run askdata eval-demo \
  --cases tests/fixtures/v2_demo_cases.json \
  --out reports/v2-demo.json
```

也可以用 `--predictions captured-predictions.json` 比较单独捕获的预测。命令只做确定性离线比较，不调用 LLM、数据库或向量服务；任一黄金旅程失败时退出码为非零。报告包含按类别通过率、澄清与不可回答检测、代理查询率、ChartSpec、检索 recall@K、流式一致性、重启持久性、延迟分位数和调用/执行/token 计数。

## BIRD Mini-Dev 固定基线

固定题集：`bird-minidev-v4pro-seed42-100.json`，模型：`deepseek-v4-pro`。

| 版本 | Relaxed EA | Strict EA | SQL 执行成功率 | 平均延迟 | P95 延迟 |
| --- | ---: | ---: | ---: | ---: | ---: |
| intern-agents 历史报告（2026-07-10） | 53% | 24% | 100% | 18,358.53 ms | 48,735.66 ms |
| askdata-v1 当前实现（2026-07-14） | 56% | 27% | 100% | 15,580.63 ms | 31,414.75 ms |

固定 manifest 复测命令：

```bash
uv run askdata eval-bird \
  --model-name deepseek-v4-pro \
  --question-manifest benchmarks/bird-minidev-v4pro-seed42-100.json \
  --out reports/bird-eval-v4pro-100.json
```

完整报告保持 gitignored；报告会记录 manifest hash、processed 数据指纹、模型名、strict/relaxed EA、失败桶、重试率和延迟分位数。

每次 BIRD 结果必须同时报告 Strict EA 和 Relaxed EA，并同时保留 valid SQL rate 与 exact match；不得只选择较高的单项指标。
