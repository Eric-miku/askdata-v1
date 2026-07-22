import ReactECharts from "echarts-for-react";
import { useMemo, useRef, useState } from "react";
import { buildChartOption, convertChartType, type SupportedChartType } from "../utils/chartBuilder";

interface ResultChartProps {
  chart?: Record<string, unknown> | null;
  loading?: boolean;
}

export function ResultChart({ chart, loading = false }: ResultChartProps) {
  const [selectedType, setSelectedType] = useState<SupportedChartType | null>(null);
  const instanceRef = useRef<ReactECharts>(null);
  const selectedChart = useMemo(
    () => chart && selectedType ? convertChartType(chart, selectedType) : chart,
    [chart, selectedType],
  );
  const option = useMemo(() => buildChartOption(selectedChart), [selectedChart]);

  const downloadPng = () => {
    const instance = instanceRef.current?.getEchartsInstance();
    if (!instance) return;
    const link = document.createElement("a");
    link.href = instance.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#ffffff" });
    link.download = "askdata-chart.png";
    link.click();
  };

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
      <div className="result-chart__toolbar">
        <label>
          <span>图表类型</span>
          <select value={selectedType ?? String(chart?.type ?? "bar")} onChange={(event) => setSelectedType(event.target.value as SupportedChartType)}>
            <option value="bar">柱状图</option>
            <option value="horizontal_bar">条形图</option>
            <option value="line">折线图</option>
            <option value="pie">饼图</option>
          </select>
        </label>
        <button type="button" onClick={downloadPng}>导出 PNG</button>
      </div>
      {option ? (
        <ReactECharts
          ref={instanceRef}
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
