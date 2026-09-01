"use client";

import { useState } from "react";
import { ChevronDown, HelpCircle } from "lucide-react";

import { AddToEvaluation } from "@/components/knowledge/add-to-evaluation";
import type { AnalyticsResponse, LineageMetricNode } from "@/lib/types/analytics";

/**
 * Why this answer.
 *
 * A summary a non-technical reader can follow, with the technical detail folded
 * away behind it. Everything shown is derived from what the backend recorded —
 * the statement that ran, the confirmed semantic model, the metric's registered
 * dependencies — so nothing here is a story the model told about itself.
 *
 * The SQL appears only when the backend chose to include it, which it does under
 * the same policy that gates debug provenance.
 */
interface AnswerTraceProps {
  question: string;
  response: AnalyticsResponse;
}

export function AnswerTrace({ question, response }: AnswerTraceProps) {
  const [open, setOpen] = useState(false);
  const trace = response.trace;
  if (trace === null || trace === undefined) return null;

  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-start"
      >
        <span className="inline-flex items-center gap-2 text-[13px] font-medium">
          <HelpCircle className="size-3.5 text-muted-foreground" aria-hidden="true" />
          Why this answer?
        </span>
        <ChevronDown
          className={`size-4 shrink-0 text-muted-foreground transition-transform ${
            open ? "rotate-180" : ""
          }`}
          aria-hidden="true"
        />
      </button>

      {open ? (
        <div className="space-y-4 border-t border-border px-4 py-4">
          <p className="text-[13px] text-muted-foreground">{summary(trace)}</p>

          <Section title="Answer path">
            <Row label="Data source" value={trace.data_source} />
            <Row label="Route" value={routeLabel(trace.route)} />
            <Row label="Executed by" value={trace.execution_source} />
            <Row
              label="Answer checked against the result"
              value={trace.grounded ? "yes" : "no"}
            />
            <Row label="SQL validation" value={trace.validation_status} />
            <Row label="Model" value={trace.model_profile} />
          </Section>

          {trace.time !== null ? (
            <Section title="Time interpretation">
              <Row label="You asked for" value={trace.time.phrase || trace.time.label} />
              <Row label="Read as" value={trace.time.label} />
              <Row label="Time zone" value={trace.time.timezone} />
              <Row
                label="Period"
                value={`${local(trace.time.start, trace.time.timezone)} to ${local(
                  trace.time.end,
                  trace.time.timezone,
                )}`}
              />
              {trace.time.comparison_start !== null &&
              trace.time.comparison_end !== null ? (
                <Row
                  label={trace.time.comparison_label || "Compared with"}
                  value={`${local(
                    trace.time.comparison_start,
                    trace.time.timezone,
                  )} to ${local(trace.time.comparison_end, trace.time.timezone)}`}
                />
              ) : null}
              {trace.time.time_dimension !== "" ? (
                <Row label="Measured on" value={trace.time.time_dimension} />
              ) : null}
              {trace.time.grain !== "NONE" ? (
                <Row label="Grouped by" value={trace.time.grain.toLowerCase()} />
              ) : null}
              {trace.time.fiscal ? (
                <Row label="Calendar" value={`fiscal (${trace.time.policy_status})`} />
              ) : null}
            </Section>
          ) : null}

          {trace.metrics.length > 0 ? (
            <Section title="Metrics">
              {trace.metric_lineage.map((node) => (
                <MetricTree key={node.label} node={node} depth={0} />
              ))}
            </Section>
          ) : null}

          {trace.business_instructions.length > 0 ? (
            <Section title="Business rules applied">
              {trace.business_instructions.map((title) => (
                <p key={title} className="text-[13px]">
                  {title}
                </p>
              ))}
            </Section>
          ) : null}

          {trace.resolved_entities.length > 0 ? (
            <Section title="Entities">
              {trace.resolved_entities.map((entity) => (
                <p key={entity} className="font-mono text-xs">
                  {entity}
                </p>
              ))}
            </Section>
          ) : null}

          {trace.tables.length > 0 ? (
            <Section title={trace.column_level ? "Tables and columns" : "Tables"}>
              {trace.tables.map((table) => (
                <div key={table.table} className="text-[13px]">
                  <span className="font-mono text-xs">{table.table}</span>
                  {table.entity !== null ? (
                    <span className="text-muted-foreground"> · {table.entity}</span>
                  ) : null}
                  {table.columns.length > 0 ? (
                    <span className="block font-mono text-xs text-muted-foreground">
                      {table.columns.join(", ")}
                    </span>
                  ) : null}
                </div>
              ))}
              {trace.lineage_note !== "" ? (
                <p className="text-xs text-muted-foreground">{trace.lineage_note}</p>
              ) : null}
            </Section>
          ) : null}

          {trace.data_quality.length > 0 ? (
            <Section title="Data quality">
              {trace.data_quality.map((warning) => (
                <p key={warning.table} className="text-[13px]">
                  <span className="font-mono text-xs">{warning.table}</span>{" "}
                  <span className="text-muted-foreground">{warning.message}</span>
                </p>
              ))}
            </Section>
          ) : null}

          {trace.generated_sql !== null && trace.generated_sql !== undefined ? (
            <Section title="Statement">
              <pre className="overflow-x-auto rounded bg-muted/50 p-3 font-mono text-xs">
                {trace.generated_sql}
              </pre>
            </Section>
          ) : null}

          <div className="border-t border-border pt-3">
            <AddToEvaluation question={question} response={response} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function summary(trace: NonNullable<AnalyticsResponse["trace"]>): string {
  const source = trace.data_source;
  const path =
    trace.route === "governed_metric"
      ? "a certified metric"
      : "SQL written for this question and validated before it ran";
  const tables =
    trace.tables.length > 0
      ? ` It read ${trace.tables.length} table${trace.tables.length === 1 ? "" : "s"}.`
      : "";
  const rules =
    trace.business_instructions.length > 0
      ? ` ${trace.business_instructions.length} approved business rule${
          trace.business_instructions.length === 1 ? "" : "s"
        } shaped it.`
      : "";
  return `Answered from ${source} using ${path}.${tables}${rules}`;
}

/** Local wall-clock, because the period was decided in the datasource's zone. */
function local(instant: string, timezone: string): string {
  try {
    return new Date(instant).toLocaleString(undefined, {
      timeZone: timezone,
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return instant;
  }
}

function routeLabel(route: string): string {
  return route === "governed_metric" ? "governed metric" : "ad-hoc analysis";
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className="label-xs text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <p className="flex flex-wrap justify-between gap-2 text-[13px]">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono text-xs">{value}</span>
    </p>
  );
}

function MetricTree({ node, depth }: { node: LineageMetricNode; depth: number }) {
  return (
    <div style={{ paddingInlineStart: `${depth * 14}px` }}>
      <p className="font-mono text-xs">
        {depth > 0 ? "└─ " : ""}
        {node.label}
        {node.kind !== "metric" ? (
          <span className="text-muted-foreground"> ({node.kind})</span>
        ) : null}
      </p>
      {node.children.map((child) => (
        <MetricTree key={`${child.label}-${depth}`} node={child} depth={depth + 1} />
      ))}
    </div>
  );
}
