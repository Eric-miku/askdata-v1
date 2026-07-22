import type { EChartsOption } from "echarts";

type ChartRecord = Record<string, unknown>;
type RowValue = string | number | boolean | null | undefined | Record<string, unknown> | unknown[];
type ResultRows = Record<string, RowValue>[] | RowValue[][] | null | undefined;

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

function buildHorizontalOption(chart: ChartRecord): EChartsOption {
  return {
    title: buildTitle(chart),
    tooltip: { trigger: "axis" },
    legend: { type: "scroll", top: chart.title ? 28 : 0 },
    grid: { top: chart.title ? 68 : 40, right: 28, bottom: 28, left: 32, containLabel: true },
    xAxis: { type: "value", ...asRecord(chart.xAxis) },
    yAxis: { type: "category", ...asRecord(chart.yAxis), axisLabel: { hideOverlap: true } },
    series: normalizeSeries(chart).map((series) => ({ ...series, type: "bar" })),
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

  const type = getChartType(chart);
  if (type === "pie") return buildPieOption(chart);
  if (type === "horizontal_bar") return buildHorizontalOption(chart);
  return buildAxisOption(chart);
}

export type SupportedChartType = "bar" | "horizontal_bar" | "line" | "pie";

export function convertChartType(chart: Record<string, unknown>, type: SupportedChartType): ChartRecord {
  const source = chart as ChartRecord;
  const currentType = getChartType(source);
  const sourceSeries = normalizeSeries(source)[0] ?? {};
  let categories: unknown[] = [];
  let values: unknown[] = [];
  if (currentType === "pie") {
    const points = Array.isArray(sourceSeries.data) ? sourceSeries.data : [];
    categories = points.map((point) => isRecord(point) ? point.name : "");
    values = points.map((point) => isRecord(point) ? point.value : null);
  } else if (currentType === "horizontal_bar") {
    const axis = asRecord(source.yAxis);
    categories = Array.isArray(axis.data) ? axis.data : [];
    values = Array.isArray(sourceSeries.data) ? sourceSeries.data : [];
  } else {
    const axis = asRecord(source.xAxis);
    categories = Array.isArray(axis.data) ? axis.data : [];
    values = Array.isArray(sourceSeries.data) ? sourceSeries.data : [];
  }
  const name = typeof sourceSeries.name === "string" ? sourceSeries.name : String(source.metric ?? "数值");
  const base: ChartRecord = { ...source, type };
  if (type === "pie") {
    return { ...base, xAxis: undefined, yAxis: undefined, series: [{ name, data: categories.map((category, index) => ({ name: String(category), value: values[index] })) }] };
  }
  if (type === "horizontal_bar") {
    return { ...base, xAxis: { type: "value", name }, yAxis: { type: "category", data: categories }, series: [{ name, data: values }] };
  }
  return { ...base, xAxis: { type: "category", data: categories }, yAxis: { type: "value", name }, series: [{ name, data: values }] };
}

function isPrimitiveCategory(value: unknown): value is string | number | boolean {
  return ["string", "number", "boolean"].includes(typeof value);
}

function getCell(row: Record<string, RowValue> | RowValue[], column: string, index: number): RowValue {
  return Array.isArray(row) ? row[index] : row[column];
}

export function buildChartFromRows(
  columns: string[] | null | undefined,
  rows: ResultRows,
): ChartRecord | null {
  if (!columns?.length || !Array.isArray(rows) || rows.length === 0) {
    return null;
  }

  const sample = rows.slice(0, 50);
  const numericColumn = columns.find((column, index) =>
    sample.some((row) => typeof getCell(row, column, index) === "number"),
  );
  if (!numericColumn) {
    return null;
  }
  const numericIndex = columns.indexOf(numericColumn);
  const categoryColumn = columns.find((column, index) =>
    column !== numericColumn && sample.some((row) => isPrimitiveCategory(getCell(row, column, index))),
  );
  const categoryIndex = categoryColumn ? columns.indexOf(categoryColumn) : -1;

  return {
    type: "bar",
    xAxis: {
      data: sample.map((row, index) => {
        const value = categoryColumn ? getCell(row, categoryColumn, categoryIndex) : index + 1;
        return String(value ?? index + 1);
      }),
    },
    yAxis: {
      name: numericColumn,
    },
    series: [
      {
        name: numericColumn,
        data: sample.map((row) => {
          const value = getCell(row, numericColumn, numericIndex);
          return typeof value === "number" ? value : null;
        }),
      },
    ],
  };
}
