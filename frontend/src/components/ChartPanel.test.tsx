import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChartSpec, QueryCellValue } from "../types/query";

const mockedReactECharts = vi.hoisted(() => vi.fn(() => <div data-testid="echart" />));
vi.mock("echarts-for-react", () => ({ default: mockedReactECharts }));

import ChartPanel, { buildChartOption } from "./ChartPanel";

const rankingSpec: ChartSpec = {
  type: "horizontal_bar",
  title: "Top schools by enrollment",
  category_field: "school",
  category_label: "School",
  value_fields: ["enrollment"],
  value_labels: { enrollment: "Students" },
  reason: "ranking",
};
const rankingRows = [
  { school: "North", enrollment: 320, ignored: "private" },
  { school: "South", enrollment: 240, ignored: "private" },
];

describe("ChartPanel", () => {
  beforeEach(() => mockedReactECharts.mockClear());
  afterEach(() => vi.restoreAllMocks());

  it("maps a ranking spec to a horizontal bar using only named fields", () => {
    const option = buildChartOption(rankingSpec, rankingRows, {
      text: "#222",
      muted: "#777",
      border: "#ddd",
      accent: "#d97757",
      surface: "#fff",
    });

    expect(option).toMatchObject({
      xAxis: { type: "value" },
      yAxis: { type: "category", data: ["North", "South"] },
      series: [{ type: "bar", data: [320, 240], name: "Students" }],
    });
    expect(JSON.stringify(option)).not.toContain("private");
  });

  it("renders a responsive labelled chart and screen-reader summary", () => {
    const { container } = render(
      <ChartPanel spec={rankingSpec} rows={rankingRows} />,
    );

    expect(mockedReactECharts).toHaveBeenCalledWith(
      expect.objectContaining({
        option: expect.objectContaining({ yAxis: expect.any(Object) }),
        style: expect.objectContaining({ width: "100%" }),
      }),
      expect.any(Object),
    );
    expect(container.querySelector(".chart-panel")).toBeInTheDocument();
    const chart = screen.getByRole("img", { name: "Top schools by enrollment" });
    const summaryId = chart.getAttribute("aria-describedby");
    expect(summaryId).toBeTruthy();
    const summary = document.getElementById(summaryId!);
    expect(summary).toHaveTextContent(/2 个数据点/);
    expect(summary).toHaveClass("visually-hidden");
  });

  it("disables ECharts animation when reduced motion is requested", () => {
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query) =>
        ({
          matches: query === "(prefers-reduced-motion: reduce)",
          media: query,
          onchange: null,
          addListener: vi.fn(),
          removeListener: vi.fn(),
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
          dispatchEvent: vi.fn(),
        }) as MediaQueryList,
    );

    render(<ChartPanel spec={rankingSpec} rows={rankingRows} />);

    expect(mockedReactECharts).toHaveBeenCalledWith(
      expect.objectContaining({
        option: expect.objectContaining({ animation: false, animationDuration: 0 }),
      }),
      expect.any(Object),
    );
  });

  it("keeps restrained animation when reduced motion is not requested", () => {
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query) =>
        ({
          matches: false,
          media: query,
          onchange: null,
          addListener: vi.fn(),
          removeListener: vi.fn(),
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
          dispatchEvent: vi.fn(),
        }) as MediaQueryList,
    );

    render(<ChartPanel spec={rankingSpec} rows={rankingRows} />);

    expect(mockedReactECharts).toHaveBeenCalledWith(
      expect.objectContaining({
        option: expect.objectContaining({ animation: true, animationDuration: 240 }),
      }),
      expect.any(Object),
    );
  });

  it("builds line, vertical bar, pie, and scatter options without executable formatters", () => {
    const base = { ...rankingSpec, title: "Chart" };
    const cases: Array<[ChartSpec, Record<string, QueryCellValue>[]]> = [
      [{ ...base, type: "line", reason: "time_series" }, rankingRows],
      [{ ...base, type: "vertical_bar", reason: "comparison" }, rankingRows],
      [{ ...base, type: "pie", reason: "proportion" }, rankingRows],
      [
        {
          ...base,
          type: "scatter",
          reason: "correlation",
          category_field: null,
          category_label: null,
          value_fields: ["x", "y"],
          value_labels: { x: "X", y: "Y" },
        },
        [{ x: 1, y: 2, unsafe: "() => alert(1)" }],
      ],
    ];

    for (const [spec, rows] of cases) {
      const serialized = JSON.stringify(buildChartOption(spec, rows));
      expect(serialized).not.toContain("formatter");
      expect(serialized).not.toContain("unsafe");
    }
  });
});
