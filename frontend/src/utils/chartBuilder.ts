import type { EChartsOption } from "echarts";

type ChartRecord = Record<string, unknown>;

function isRecord(value: unknown): value is ChartRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasSeries(value: unknown): value is { series: unknown } {
  return isRecord(value) && "series" in value;
}

function asRecord(value: unknown): ChartRecord {
  return isRecord(value) ? value : {};
}

function getChartType(chart: ChartRecord): string {
  return typeof chart.type === "string" ? chart.type : "bar";
}

function buildTitle(chart: ChartRecord): EChartsOption["title"] {
  if (!chart.title) {
    return undefined;
  }
  if (typeof chart.title === "string") {
    return {
      text: chart.title,
      left: "center",
      top: 0,
      textStyle: {
        fontSize: 14,
        fontWeight: 600,
      },
    };
  }
  return chart.title as EChartsOption["title"];
}

function normalizeSeries(chart: ChartRecord): ChartRecord[] {
  const type = getChartType(chart);
  return Array.isArray(chart.series)
    ? chart.series.map((item) => {
        const series = asRecord(item);
        return {
          ...series,
          type: typeof series.type === "string" ? series.type : type,
          data: Array.isArray(series.data) ? series.data : [],
          smooth:
            series.smooth ??
            (type === "line" || series.type === "line" ? true : undefined),
        };
      })
    : [];
}

function buildPieOption(chart: ChartRecord): EChartsOption {
  return {
    title: buildTitle(chart),
    tooltip: chart.tooltip ?? {
      trigger: "item",
    },
    legend: chart.legend ?? {
      type: "scroll",
      bottom: 0,
    },
    series: normalizeSeries(chart).map((series) => ({
      radius: ["38%", "66%"],
      center: ["50%", "46%"],
      ...series,
      type: "pie",
    })),
  } as EChartsOption;
}

function buildAxisOption(chart: ChartRecord): EChartsOption {
  const xAxis = asRecord(chart.xAxis);
  const hasManyCategories = Array.isArray(xAxis.data) && xAxis.data.length > 12;

  return {
    title: buildTitle(chart),
    tooltip: chart.tooltip ?? {
      trigger: "axis",
    },
    legend: chart.legend ?? {
      type: "scroll",
      top: chart.title ? 28 : 0,
    },
    grid: {
      top: chart.title ? 68 : 40,
      right: 24,
      bottom: hasManyCategories ? 62 : 38,
      left: 44,
      containLabel: true,
    },
    xAxis: {
      type: "category",
      ...xAxis,
      axisLabel: {
        interval: 0,
        hideOverlap: true,
        ...(isRecord(xAxis.axisLabel) ? xAxis.axisLabel : {}),
      },
    },
    yAxis: {
      type: "value",
      ...asRecord(chart.yAxis),
    },
    dataZoom: hasManyCategories
      ? [
          {
            type: "inside",
          },
          {
            type: "slider",
            height: 20,
            bottom: 18,
          },
        ]
      : undefined,
    series: normalizeSeries(chart),
  } as EChartsOption;
}

export function buildChartOption(chart: unknown): EChartsOption | null {
  if (!isRecord(chart)) {
    return null;
  }

  if (hasSeries(chart) && !("type" in chart)) {
    return chart as EChartsOption;
  }

  if (!Array.isArray(chart.series) || chart.series.length === 0) {
    return null;
  }

  return getChartType(chart) === "pie"
    ? buildPieOption(chart)
    : buildAxisOption(chart);
}
