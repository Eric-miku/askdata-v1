import ReactECharts from "echarts-for-react";
import type { ChartSpec, QueryCellValue } from "../types/query";

export interface ChartTheme {
  text: string;
  muted: string;
  border: string;
  accent: string;
  surface: string;
}

type ChartRow = Record<string, QueryCellValue>;
type ChartOption = Record<string, unknown>;

const fallbackTheme: ChartTheme = {
  text: "#deddd9",
  muted: "#8c8b86",
  border: "#353534",
  accent: "#d97757",
  surface: "#262625",
};

function cssTheme(): ChartTheme {
  if (typeof window === "undefined") return fallbackTheme;
  const styles = window.getComputedStyle(document.documentElement);
  const token = (name: string, fallback: string) =>
    styles.getPropertyValue(name).trim() || fallback;
  return {
    text: token("--text", fallbackTheme.text),
    muted: token("--text-muted", fallbackTheme.muted),
    border: token("--border", fallbackTheme.border),
    accent: token("--accent", fallbackTheme.accent),
    surface: token("--surface", fallbackTheme.surface),
  };
}

function textValue(value: QueryCellValue): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}

function numericValue(value: QueryCellValue): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function sharedOption(spec: ChartSpec, theme: ChartTheme): ChartOption {
  return {
    animationDuration: 240,
    backgroundColor: "transparent",
    color: [theme.accent],
    textStyle: { color: theme.text, fontFamily: "Inter, system-ui, sans-serif" },
    tooltip: { trigger: spec.type === "pie" ? "item" : "axis" },
  };
}

function cartesianBase(theme: ChartTheme): ChartOption {
  return {
    grid: { top: 24, right: 20, bottom: 42, left: 54, containLabel: true },
    legend: { bottom: 0, textStyle: { color: theme.muted } },
  };
}

function categoryValues(spec: ChartSpec, rows: ChartRow[]): string[] {
  if (!spec.category_field) return [];
  return rows.map((row) => textValue(row[spec.category_field!]));
}

function valueSeries(spec: ChartSpec, rows: ChartRow[], type: "line" | "bar") {
  return spec.value_fields.map((field) => ({
    name: spec.value_labels[field] || field,
    type,
    data: rows.map((row) => numericValue(row[field])),
    ...(type === "line" ? { smooth: false, symbolSize: 6 } : {}),
  }));
}

export function buildChartOption(
  spec: ChartSpec,
  rows: ChartRow[],
  theme: ChartTheme = fallbackTheme,
): ChartOption {
  const shared = sharedOption(spec, theme);
  const axisStyle = {
    axisLine: { lineStyle: { color: theme.border } },
    axisLabel: { color: theme.muted },
    splitLine: { lineStyle: { color: theme.border } },
  };

  if (spec.type === "pie") {
    const categoryField = spec.category_field;
    const valueField = spec.value_fields[0];
    return {
      ...shared,
      legend: { bottom: 0, textStyle: { color: theme.muted } },
      series: [
        {
          name: spec.value_labels[valueField] || valueField,
          type: "pie",
          radius: ["42%", "68%"],
          data: rows.map((row) => ({
            name: categoryField ? textValue(row[categoryField]) : "",
            value: numericValue(row[valueField]),
          })),
        },
      ],
    };
  }

  if (spec.type === "scatter") {
    const [xField, yField] = spec.value_fields;
    return {
      ...shared,
      ...cartesianBase(theme),
      xAxis: {
        type: "value",
        name: spec.value_labels[xField] || xField,
        ...axisStyle,
      },
      yAxis: {
        type: "value",
        name: spec.value_labels[yField] || yField,
        ...axisStyle,
      },
      series: [
        {
          name: `${spec.value_labels[xField] || xField} / ${spec.value_labels[yField] || yField}`,
          type: "scatter",
          symbolSize: 9,
          data: rows.map((row) => [numericValue(row[xField]), numericValue(row[yField])]),
        },
      ],
    };
  }

  const categories = categoryValues(spec, rows);
  if (spec.type === "horizontal_bar") {
    return {
      ...shared,
      ...cartesianBase(theme),
      xAxis: { type: "value", ...axisStyle },
      yAxis: {
        type: "category",
        name: spec.category_label || spec.category_field || "",
        data: categories,
        ...axisStyle,
      },
      series: valueSeries(spec, rows, "bar"),
    };
  }

  return {
    ...shared,
    ...cartesianBase(theme),
    xAxis: {
      type: "category",
      name: spec.category_label || spec.category_field || "",
      data: categories,
      boundaryGap: spec.type === "vertical_bar",
      ...axisStyle,
    },
    yAxis: { type: "value", ...axisStyle },
    series: valueSeries(spec, rows, spec.type === "line" ? "line" : "bar"),
  };
}

interface ChartPanelProps {
  spec: ChartSpec;
  rows: ChartRow[];
}

export default function ChartPanel({ spec, rows }: ChartPanelProps) {
  const summary = `${spec.title}，${rows.length} 个数据点，图表类型为${
    spec.type === "line"
      ? "折线图"
      : spec.type === "vertical_bar"
        ? "柱状图"
        : spec.type === "horizontal_bar"
          ? "水平条形图"
          : spec.type === "pie"
            ? "环形图"
            : "散点图"
  }。`;

  return (
    <figure className="chart-panel" role="img" aria-label={spec.title}>
      <figcaption>{spec.title}</figcaption>
      <ReactECharts
        option={buildChartOption(spec, rows, cssTheme())}
        notMerge
        lazyUpdate
        style={{ width: "100%", height: "320px" }}
      />
      <span className="visually-hidden">{summary}</span>
    </figure>
  );
}
