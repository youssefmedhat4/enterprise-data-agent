"use client";

import { AnimatePresence, motion } from "motion/react";
import { BarChart3, Table2 } from "lucide-react";
import dynamic from "next/dynamic";
import { useId, useState } from "react";

import { DataTable } from "@/components/tables/data-table";
import { DUR, EASE_OUT, SPRING } from "@/lib/motion";
import { cn } from "@/lib/utils";
import type { AnalyticsResponse } from "@/lib/types/analytics";

/** Recharts is heavy; it stays out of the initial bundle. */
const AnalyticsChart = dynamic(
  () => import("@/components/charts/analytics-chart").then((m) => m.AnalyticsChart),
  {
    ssr: false,
    loading: () => <ChartSkeleton />,
  },
);

function ChartSkeleton() {
  return (
    <div className="relative aspect-[16/7] min-h-[240px] w-full overflow-hidden">
      <div className="animate-sheen absolute inset-0 overflow-hidden" />
      <div className="absolute inset-x-6 bottom-10 flex items-end gap-3">
        {[52, 78, 40, 64, 30].map((height, index) => (
          <div
            key={index}
            className="flex-1 rounded-t bg-muted"
            style={{ height: `${height}%` }}
          />
        ))}
      </div>
      <div className="absolute inset-x-6 bottom-6 h-px bg-border" />
    </div>
  );
}

/**
 * The evidence surface for one analysis.
 *
 * A single framed workspace with its own header strip, rather than a chart card
 * plus a separate table card. Chart and table are two views of the same result,
 * so they share one frame and one row count — nothing reads as hidden.
 */
export function EvidencePanel({ response }: { response: AnalyticsResponse }) {
  const hasChart = response.chart !== null;
  const [view, setView] = useState<"chart" | "table">(
    hasChart ? "chart" : "table",
  );
  const baseId = useId();

  if (response.columns.length === 0) return null;

  const views = (
    [
      { id: "chart" as const, label: "Chart", Icon: BarChart3 },
      { id: "table" as const, label: "Data", Icon: Table2 },
    ] as const
  ).filter((candidate) => candidate.id !== "chart" || hasChart);

  return (
    <section className="overflow-hidden rounded-xl border border-border bg-surface shadow-raise">
      {/* Header strip: title on the left, view switch on the right. */}
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-surface-raised/40 px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <h3
            dir="auto"
            className="truncate text-[13px] font-medium text-foreground"
          >
            {response.chart?.title ?? "Result"}
          </h3>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            <span className="tnum">{response.execution.row_count}</span>
            {response.execution.row_count === 1 ? " row" : " rows"}
            <span aria-hidden="true"> · </span>
            <span className="tnum">{response.columns.length}</span>
            {response.columns.length === 1 ? " column" : " columns"}
            {response.execution.truncated ? (
              <span className="text-warning"> · truncated</span>
            ) : null}
          </p>
        </div>

        {views.length > 1 ? (
          <div
            role="tablist"
            aria-label="Result view"
            className="relative flex shrink-0 items-center gap-0.5 rounded-lg bg-muted p-0.5"
          >
            {views.map(({ id, label, Icon }) => {
              const selected = view === id;
              return (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  id={`${baseId}-tab-${id}`}
                  aria-selected={selected}
                  aria-controls={`${baseId}-panel-${id}`}
                  onClick={() => setView(id)}
                  className={cn(
                    "relative flex items-center gap-1.5 rounded-[7px] px-2.5 py-1 text-[12px] font-medium transition-colors",
                    selected
                      ? "text-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {selected ? (
                    <motion.span
                      layoutId={`${baseId}-view-pill`}
                      transition={SPRING}
                      aria-hidden="true"
                      className="absolute inset-0 rounded-[7px] bg-surface shadow-raise"
                    />
                  ) : null}
                  <Icon className="relative size-3.5" aria-hidden="true" />
                  <span className="relative">{label}</span>
                </button>
              );
            })}
          </div>
        ) : null}
      </header>

      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={view}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: DUR.fast, ease: EASE_OUT }}
          role="tabpanel"
          id={`${baseId}-panel-${view}`}
          aria-labelledby={views.length > 1 ? `${baseId}-tab-${view}` : undefined}
        >
          {view === "chart" && response.chart !== null ? (
            <div className="px-4 py-5">
              <AnalyticsChart spec={response.chart} rows={response.rows} />
            </div>
          ) : (
            <DataTable
              columns={response.columns}
              rows={response.rows}
              columnTypes={response.provenance.result.column_types}
              caption={response.chart?.title ?? "Analysis result"}
            />
          )}
        </motion.div>
      </AnimatePresence>
    </section>
  );
}
