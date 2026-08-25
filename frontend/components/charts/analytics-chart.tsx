"use client";

import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  formatCompact,
  formatNumber,
  humanizeColumn,
  toNumber,
} from "@/lib/format/values";
import type { ChartSpec, ResultRow } from "@/lib/types/analytics";

/**
 * Renders the backend's validated ChartSpec.
 *
 * The spec is authoritative: the backend has already confirmed the fields exist
 * in `rows`, that the measure is numeric, and that pie/donut measures are
 * non-negative. Nothing here derives a new measure — values come straight from
 * the returned rows and are only coerced from Decimal-as-string to number.
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
const MAX_SLICES = 8;

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

function prepare(spec: ChartSpec, rows: ResultRow[]): Prepared {
  const { x, y, series } = spec;

  if (series === null) {
    const data: Record<string, string | number>[] = [];
    for (const row of rows) {
      const value = toNumber(row[y] ?? null);
      if (value === null) continue;
      data.push({ [x]: String(row[x] ?? "—"), [y]: value });
    }
    const limited = data.slice(0, MAX_CATEGORIES);
    return {
      data: limited,
      seriesKeys: [y],
      colorFor: new Map([[y, SERIES_COLORS[0]]]),
      omitted: data.length - limited.length,
    };
  }

  const byCategory = new Map<string, Record<string, string | number>>();
  const seriesKeys: string[] = [];

  for (const row of rows) {
    const value = toNumber(row[y] ?? null);
    if (value === null) continue;
    const category = String(row[x] ?? "—");
    const group = String(row[series] ?? "—");
    if (!seriesKeys.includes(group)) seriesKeys.push(group);
    const entry = byCategory.get(category) ?? { [x]: category };
    entry[group] = value;
    byCategory.set(category, entry);
  }

  const limitedKeys = seriesKeys.slice(0, SERIES_COLORS.length);
  const colorFor = new Map(
    limitedKeys.map((key, index) => [key, SERIES_COLORS[index]] as const),
  );
  const data = [...byCategory.values()].slice(0, MAX_CATEGORIES);

  return {
    data,
    seriesKeys: limitedKeys,
    colorFor,
    omitted:
      byCategory.size - data.length + (seriesKeys.length - limitedKeys.length),
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
}: {
  active?: boolean;
  payload?: { name?: string; value?: number; color?: string }[];
  label?: string;
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
              {item.value === undefined ? "—" : formatNumber(item.value)}
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
  const showLegend = seriesKeys.length > 1;

  const circularData = isCircular
    ? data
        .map((entry) => ({
          name: String(entry[spec.x]),
          value: Number(entry[seriesKeys[0]] ?? 0),
        }))
        .slice(0, MAX_SLICES)
    : [];

  return (
    <figure className="min-w-0">
      <figcaption className="sr-only">
        {spec.title}. {humanizeColumn(spec.y)}
        {isCircular ? "" : ` by ${humanizeColumn(spec.x).toLowerCase()}`}.
      </figcaption>

      <div className="h-[clamp(240px,26vh,340px)] w-full">
        <ResponsiveContainer width="100%" height="100%">
          {isCircular ? (
            <PieChart>
              <Tooltip content={<ChartTooltip />} cursor={false} />
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
          ) : spec.type === "line" ? (
            <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid
                vertical={false}
                stroke="var(--hairline)"
                strokeDasharray="0"
              />
              <XAxis
                dataKey={spec.x}
                tick={AXIS_TICK}
                tickMargin={10}
                tickFormatter={truncateLabel}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={AXIS_TICK}
                tickMargin={8}
                width={52}
                tickFormatter={(value: number) => formatCompact(value)}
              />
              <Tooltip
                content={<ChartTooltip />}
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
          ) : (
            <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid
                vertical={false}
                stroke="var(--hairline)"
                strokeDasharray="0"
              />
              <XAxis
                dataKey={spec.x}
                tick={AXIS_TICK}
                tickMargin={10}
                tickFormatter={truncateLabel}
                interval={0}
              />
              <YAxis
                tick={AXIS_TICK}
                tickMargin={8}
                width={52}
                tickFormatter={(value: number) => formatCompact(value)}
              />
              <Tooltip
                content={<ChartTooltip />}
                cursor={{ fill: "var(--muted)", opacity: 0.5 }}
              />
              {seriesKeys.map((key) => (
                <Bar
                  key={key}
                  dataKey={key}
                  fill={colorFor.get(key)}
                  radius={[4, 4, 0, 0]}
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
