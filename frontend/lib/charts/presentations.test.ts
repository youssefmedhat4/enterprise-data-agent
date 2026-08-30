import { describe, expect, it } from "vitest";

import { chartPresentations, identify } from "@/lib/charts/presentations";
import type { ChartSpec, ResultRow } from "@/lib/types/analytics";

function spec(patch: Partial<ChartSpec> = {}): ChartSpec {
  return {
    type: "bar",
    title: "Payroll by department",
    x: "department",
    measures: ["payroll"],
    series: null,
    orientation: "vertical",
    mode: "grouped",
    x_label: null,
    y_label: null,
    value_format: "currency",
    part_to_whole_display: "value_and_percent",
    sort: "none",
    limit: null,
    ...patch,
  };
}

const CATEGORY_ROWS: ResultRow[] = [
  { department: "Engineering", payroll: "710000.00" },
  { department: "Sales", payroll: "375000.00" },
  { department: "Finance", payroll: "255000.00" },
];

function ids(options: { id: string }[]): string[] {
  return options.map((option) => option.id);
}

describe("compatible presentations", () => {
  it("leads with the AI recommendation and preserves its exact spec", () => {
    const recommended = spec({ orientation: "horizontal" });

    const [first] = chartPresentations(recommended, CATEGORY_ROWS);

    expect(first.recommended).toBe(true);
    expect(first.id).toBe("bar-horizontal");
    // The server object is passed through untouched, not reconstructed.
    expect(first.spec).toBe(recommended);
  });

  it("offers bar, horizontal bar and part-to-whole for one non-negative measure", () => {
    const options = ids(chartPresentations(spec(), CATEGORY_ROWS));

    expect(options).toEqual(
      expect.arrayContaining(["bar", "bar-horizontal", "pie", "donut"]),
    );
    expect(options).not.toContain("scatter");
  });

  it("never offers part-to-whole when a value is negative", () => {
    const rows: ResultRow[] = [
      { department: "Engineering", payroll: 100 },
      { department: "Sales", payroll: -50 },
    ];

    const options = ids(chartPresentations(spec(), rows));

    expect(options).not.toContain("pie");
    expect(options).not.toContain("donut");
  });

  it("never offers part-to-whole above the backend slice limit", () => {
    const rows: ResultRow[] = Array.from({ length: 13 }, (_, index) => ({
      department: `Team ${index}`,
      payroll: index + 1,
    }));

    const options = ids(chartPresentations(spec(), rows));

    expect(options).not.toContain("pie");
  });

  it("offers grouped and stacked bar only for multi-series results", () => {
    const rows: ResultRow[] = [
      { month: "2026-01", revenue: 100, cost: 60 },
      { month: "2026-02", revenue: 140, cost: 70 },
    ];
    const multi = spec({ type: "line", x: "month", measures: ["revenue", "cost"] });

    const options = ids(chartPresentations(multi, rows));

    expect(options).toEqual(
      expect.arrayContaining(["bar-grouped", "bar-stacked", "line", "area"]),
    );
    // A single-series "Bar" option would be wrong here.
    expect(options).not.toContain("bar");
  });

  it("withholds stacking when a series carries negative values", () => {
    const rows: ResultRow[] = [
      { month: "2026-01", revenue: 100, margin: -20 },
      { month: "2026-02", revenue: 140, margin: 30 },
    ];
    const multi = spec({ type: "line", x: "month", measures: ["revenue", "margin"] });

    const options = ids(chartPresentations(multi, rows));

    expect(options).toContain("bar-grouped");
    expect(options).not.toContain("bar-stacked");
  });

  it("offers line and area only when the axis is ordered", () => {
    const categorical = ids(chartPresentations(spec(), CATEGORY_ROWS));
    expect(categorical).not.toContain("line");

    const temporal = ids(
      chartPresentations(
        spec({ x: "month", measures: ["revenue"] }),
        [
          { month: "2026-01", revenue: 100 },
          { month: "2026-02", revenue: 140 },
        ],
        { month: "date" },
      ),
    );
    expect(temporal).toEqual(expect.arrayContaining(["line", "area"]));
  });

  it("offers scatter only when x is numeric", () => {
    const rows: ResultRow[] = [
      { cost: 100, margin: 20 },
      { cost: 200, margin: 45 },
    ];

    const numericX = ids(
      chartPresentations(spec({ x: "cost", measures: ["margin"] }), rows),
    );
    expect(numericX).toContain("scatter");

    const textX = ids(chartPresentations(spec(), CATEGORY_ROWS));
    expect(textX).not.toContain("scatter");
  });

  it("returns nothing to choose between when there are no rows", () => {
    expect(chartPresentations(spec(), [])).toEqual([]);
  });

  it("only ever transforms the spec, never the rows", () => {
    const rows = structuredClone(CATEGORY_ROWS);

    const options = chartPresentations(spec(), rows);
    for (const option of options) {
      expect(option.spec.x).toBe("department");
      expect(option.spec.measures).toEqual(["payroll"]);
    }
    expect(rows).toEqual(CATEGORY_ROWS);
  });
});

describe("presentation identity", () => {
  it.each([
    [spec(), "bar"],
    [spec({ orientation: "horizontal" }), "bar-horizontal"],
    [spec({ measures: ["a", "b"], mode: "stacked" }), "bar-stacked"],
    [spec({ measures: ["a", "b"], mode: "grouped" }), "bar-grouped"],
    [spec({ type: "donut" }), "donut"],
    [spec({ type: "scatter" }), "scatter"],
  ])("identifies %#", (candidate, expected) => {
    expect(identify(candidate)).toBe(expected);
  });
});
