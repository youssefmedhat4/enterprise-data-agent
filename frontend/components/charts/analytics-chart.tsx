"use client";

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import {
  formatCompact,
  formatMeasure,
  formatSliceLabel,
  humanizeColumn,
  toNumber,
} from "@/lib/format/values";
import type { ChartSpec, ResultRow } from "@/lib/types/analytics";

/**
 * Renders the backend's validated ChartSpec.
 *
 * The spec is authoritative: `ChartValidator` has already confirmed the fields
 * exist in `rows`, that measures are numeric, that scatter has a numeric x, and
 * that pie/donut measures are non-negative and few enough to read. Nothing here
 * derives a new measure — values come straight from the returned rows and are
 * only coerced from Decimal-as-string to number.
 *
 * Sorting and limiting are display-only and come from the spec; they reorder or
 * truncate rows that already exist and never change a value.
 *
 * The library's defaults are overridden throughout so charts belong to this
 * product rather than looking like stock Recharts.
 */

const SERIES_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
] as const;

const MAX_CATEGORIES = 24;
const MAX_SLICES = 12;

interface AnalyticsChartProps {
  spec: ChartSpec;
  rows: ResultRow[];
}

interface Prepared {
  data: Record<string, string | number>[];
  seriesKeys: string[];
  colorFor: Map<string, string>;
  omitted: number;
}

/**
 * Format a plotted value for tooltips.
 *
 * `partToWholeTotal` is supplied only for pie and donut, where a slice is also
 * labelled with its share of the plotted total. That share is derived here for
 * display and never touches the returned rows.
 */
function formatValue(
  value: number,
  spec: ChartSpec,
  partToWholeTotal?: number,
): string {
  if (partToWholeTotal !== undefined) {
    return formatSliceLabel(
      value,
      partToWholeTotal,
      spec.value_format,
      spec.part_to_whole_display,
    );
  }
  return formatMeasure(value, spec.value_format);
}

function prepare(spec: ChartSpec, rows: ResultRow[]): Prepared {
  const { x, measures, series } = spec;
  const cap = Math.min(spec.limit ?? MAX_CATEGORIES, MAX_CATEGORIES);

  let data: Record<string, string | number>[];
  let seriesKeys: string[];

  if (series !== null) {
    // Long format: pivot each distinct `series` value into its own key.
    const measure = measures[0];
    const byCategory = new Map<string, Record<string, string | number>>();
    const keys: string[] = [];

    for (const row of rows) {
      const value = toNumber(row[measure] ?? null);
      if (value === null) continue;
      const category = String(row[x] ?? "—");
      const group = String(row[series] ?? "—");
      if (!keys.includes(group)) keys.push(group);
      const entry = byCategory.get(category) ?? { [x]: category };
      entry[group] = value;
      byCategory.set(category, entry);
    }

    seriesKeys = keys.slice(0, SERIES_COLORS.length);
    data = [...byCategory.values()];
  } else {
    // Wide format: each measure column is already its own series.
    seriesKeys = measures.slice(0, SERIES_COLORS.length);
    data = [];
    for (const row of rows) {
      // Scatter needs a numeric x; every other type treats x as a category
      // label. The validator has already guaranteed x is numeric for scatter.
      const xValue =
        spec.type === "scatter"
          ? (toNumber(row[x] ?? null) ?? 0)
          : String(row[x] ?? "—");
      const entry: Record<string, string | number> = { [x]: xValue };
      let hasValue = false;
      for (const measure of seriesKeys) {
        const value = toNumber(row[measure] ?? null);
        if (value !== null) {
          entry[measure] = value;
          hasValue = true;
        }
      }
      if (hasValue) data.push(entry);
    }
  }

  if (spec.sort !== "none" && seriesKeys.length > 0) {
    const key = seriesKeys[0];
    const direction = spec.sort === "ascending" ? 1 : -1;
    data = [...data].sort(
      (left, right) => (Number(left[key] ?? 0) - Number(right[key] ?? 0)) * direction,
    );
  }

  const limited = data.slice(0, cap);
  const colorFor = new Map(
    seriesKeys.map((key, index) => [key, SERIES_COLORS[index]] as const),
  );

  return {
    data: limited,
    seriesKeys,
    colorFor,
    omitted: data.length - limited.length,
  };
}

function truncateLabel(value: string): string {
  return value.length > 14 ? `${value.slice(0, 13)}…` : value;
}

const AXIS_TICK = {
  fill: "var(--muted-foreground)",
  fontSize: 11,
} as const;

/** Custom tooltip — the library default does not match the product. */
function ChartTooltip({
  active,
  payload,
  label,
  spec,
  partToWholeTotal,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number; color?: string }[];
  label?: string;
  spec: ChartSpec;
  partToWholeTotal?: number;
}) {
  if (active !== true || payload === undefined || payload.length === 0) {
    return null;
  }
  return (
    <div className="pointer-events-none min-w-36 rounded-lg border border-border bg-popover/95 px-3 py-2 shadow-overlay backdrop-blur-sm">
      {label !== undefined ? (
        <p
          dir="auto"
          className="mb-1.5 border-b border-border pb-1.5 text-[12px] font-medium text-popover-foreground"
        >
          {label}
        </p>
      ) : null}
      <ul className="space-y-1">
        {payload.map((item, index) => (
          <li
            key={`${item.name}-${index}`}
            className="flex items-center gap-2 text-[12px]"
          >
            <span
              aria-hidden="true"
              className="size-2 shrink-0 rounded-[2px]"
              style={{ background: item.color }}
            />
            <span className="min-w-0 flex-1 truncate text-muted-foreground">
              {humanizeColumn(String(item.name ?? ""))}
            </span>
            <span className="tnum font-medium text-popover-foreground">
              {item.value === undefined
                ? "—"
                : formatValue(item.value, spec, partToWholeTotal)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Legend({
  keys,
  colorFor,
}: {
  keys: string[];
  colorFor: Map<string, string>;
}) {
  return (
    <ul className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1.5">
      {keys.map((key) => (
        <li key={key} className="flex items-center gap-1.5 text-[11px]">
          <span
            aria-hidden="true"
            className="size-2 rounded-[2px]"
            style={{ background: colorFor.get(key) }}
          />
          <span dir="auto" className="text-muted-foreground">
            {humanizeColumn(key)}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function AnalyticsChart({ spec, rows }: AnalyticsChartProps) {
  const { data, seriesKeys, colorFor, omitted } = useMemo(
    () => prepare(spec, rows),
    [spec, rows],
  );

  if (data.length === 0) {
    return (
      <p className="py-10 text-center text-[13px] text-muted-foreground">
        The chart could not be drawn because the measure held no numeric values.
      </p>
    );
  }

  const isCircular = spec.type === "pie" || spec.type === "donut";
  const isHorizontal = spec.type === "bar" && spec.orientation === "horizontal";
  const stacked = spec.mode === "stacked";
  const showLegend = seriesKeys.length > 1;
  const stackId = stacked ? "stack" : undefined;

  const tooltip = <ChartTooltip spec={spec} />;
  const grid = (
    <CartesianGrid
      vertical={isHorizontal}
      horizontal={!isHorizontal}
      stroke="var(--hairline)"
      strokeDasharray="0"
    />
  );

  // Category and value axes swap roles when bars run horizontally.
  const categoryAxisProps = {
    dataKey: spec.x,
    tick: AXIS_TICK,
    tickMargin: 10,
    tickFormatter: truncateLabel,
    label: spec.x_label
      ? { value: spec.x_label, position: "insideBottom" as const, offset: -4, fill: "var(--muted-foreground)", fontSize: 11 }
      : undefined,
  };
  const valueAxisProps = {
    tick: AXIS_TICK,
    tickMargin: 8,
    tickFormatter: (value: number) => formatCompact(value),
  };

  const circularData = isCircular
    ? data
        .map((entry) => ({
          name: String(entry[spec.x]),
          value: Number(entry[seriesKeys[0]] ?? 0),
        }))
        .slice(0, MAX_SLICES)
    : [];

  // Share is relative to what is actually drawn, so a truncated pie reports the
  // share of its visible slices rather than of a total the viewer cannot see.
  const partToWholeTotal = isCircular
    ? circularData.reduce((sum, entry) => sum + entry.value, 0)
    : undefined;

  return (
    <figure className="min-w-0">
      <figcaption className="sr-only">
        {spec.title}. {humanizeColumn(spec.measures.join(", "))}
        {isCircular ? "" : ` by ${humanizeColumn(spec.x).toLowerCase()}`}.
      </figcaption>

      <div className="h-[clamp(240px,26vh,340px)] w-full">
        <ResponsiveContainer width="100%" height="100%">
          {isCircular ? (
            <PieChart>
              <Tooltip
                content={
                  <ChartTooltip spec={spec} partToWholeTotal={partToWholeTotal} />
                }
                cursor={false}
              />
              <Pie
                data={circularData}
                dataKey="value"
                nameKey="name"
                innerRadius={spec.type === "donut" ? "56%" : 0}
                outerRadius="82%"
                paddingAngle={1.5}
                stroke="var(--surface)"
                strokeWidth={2}
                animationDuration={620}
                animationEasing="ease-out"
              >
                {circularData.map((entry, index) => (
                  <Cell
                    key={entry.name}
                    fill={SERIES_COLORS[index % SERIES_COLORS.length]}
                  />
                ))}
              </Pie>
            </PieChart>
          ) : spec.type === "scatter" ? (
            <ScatterChart margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              {grid}
              <XAxis
                type="number"
                dataKey={spec.x}
                tick={AXIS_TICK}
                tickMargin={10}
                tickFormatter={(value: number) => formatCompact(value)}
              />
              <YAxis
                type="number"
                dataKey={seriesKeys[0]}
                width={52}
                {...valueAxisProps}
              />
              <ZAxis range={[45, 45]} />
              <Tooltip
                content={tooltip}
                cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }}
              />
              <Scatter
                data={data}
                fill={colorFor.get(seriesKeys[0])}
                animationDuration={620}
                animationEasing="ease-out"
              />
            </ScatterChart>
          ) : spec.type === "line" ? (
            <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              {grid}
              <XAxis {...categoryAxisProps} interval="preserveStartEnd" />
              <YAxis width={52} {...valueAxisProps} />
              <Tooltip
                content={tooltip}
                cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }}
              />
              {seriesKeys.map((key) => (
                <Line
                  key={key}
                  dataKey={key}
                  type="monotone"
                  stroke={colorFor.get(key)}
                  strokeWidth={2}
                  dot={data.length <= 14 ? { r: 2.5, strokeWidth: 0 } : false}
                  activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
                  animationDuration={700}
                  animationEasing="ease-out"
                />
              ))}
            </LineChart>
          ) : spec.type === "area" ? (
            <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              {grid}
              <XAxis {...categoryAxisProps} interval="preserveStartEnd" />
              <YAxis width={52} {...valueAxisProps} />
              <Tooltip
                content={tooltip}
                cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }}
              />
              {seriesKeys.map((key) => (
                <Area
                  key={key}
                  dataKey={key}
                  type="monotone"
                  stackId={stackId}
                  stroke={colorFor.get(key)}
                  fill={colorFor.get(key)}
                  fillOpacity={0.18}
                  strokeWidth={2}
                  animationDuration={700}
                  animationEasing="ease-out"
                />
              ))}
            </AreaChart>
          ) : (
            <BarChart
              data={data}
              layout={isHorizontal ? "vertical" : "horizontal"}
              margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
            >
              {grid}
              {isHorizontal ? (
                <>
                  <XAxis type="number" {...valueAxisProps} />
                  <YAxis
                    type="category"
                    dataKey={spec.x}
                    tick={AXIS_TICK}
                    tickMargin={8}
                    tickFormatter={truncateLabel}
                    width={110}
                  />
                </>
              ) : (
                <>
                  <XAxis {...categoryAxisProps} interval={0} />
                  <YAxis width={52} {...valueAxisProps} />
                </>
              )}
              <Tooltip
                content={tooltip}
                cursor={{ fill: "var(--muted)", opacity: 0.5 }}
              />
              {seriesKeys.map((key) => (
                <Bar
                  key={key}
                  dataKey={key}
                  stackId={stackId}
                  fill={colorFor.get(key)}
                  radius={isHorizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]}
                  maxBarSize={48}
                  animationDuration={620}
                  animationEasing="ease-out"
                />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>

      {showLegend ? <Legend keys={seriesKeys} colorFor={colorFor} /> : null}

      {omitted > 0 ? (
        <p className="mt-3 text-[11px] text-muted-foreground">
          Showing the first {data.length} of {data.length + omitted} categories.
          The data view holds every returned row.
        </p>
      ) : null}
    </figure>
  );
}
