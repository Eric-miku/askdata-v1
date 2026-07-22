import ReactECharts from "echarts-for-react";
import { useMemo } from "react";
import { buildChartOption } from "../utils/chartBuilder";

interface ResultChartProps {
  chart?: Record<string, unknown> | null;
  loading?: boolean;
}

export function ResultChart({ chart, loading = false }: ResultChartProps) {
  const option = useMemo(() => buildChartOption(chart), [chart]);

  if (!chart && !loading) {
    return null;
  }

  if (!option && !loading) {
    return (
      <div className="result-chart__fallback" role="status">
        图表配置暂不可渲染
      </div>
    );
  }

  return (
    <div className="result-chart">
      {option ? (
        <ReactECharts
          option={option}
          notMerge
          lazyUpdate
          showLoading={loading}
          style={{ width: "100%", height: 320 }}
        />
      ) : null}
    </div>
  );
}
