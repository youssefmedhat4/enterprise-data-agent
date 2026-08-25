"use client";

import { AlertTriangle } from "lucide-react";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  formatBytes,
  formatDuration,
  formatTimestamp,
} from "@/lib/format/values";
import { isGovernedMetric, shortTableName } from "@/lib/format/source";
import type { AnalyticsResponse } from "@/lib/types/analytics";

/**
 * Sources and execution detail for one answer.
 *
 * Scoped to a single response so there is never ambiguity about what the panel
 * describes. Renders only fields present in the public contract; the `debug`
 * section appears solely when the backend itself returned authorised debug
 * data, and its SQL is displayed verbatim — never reconstructed here.
 */
interface ProvenancePanelProps {
  response: AnalyticsResponse | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[7.5rem_1fr] gap-3 py-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-[13px] text-foreground">{children}</dd>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-border pt-4">
      <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      <dl className="divide-y divide-border/50">{children}</dl>
    </section>
  );
}

export function ProvenancePanel({
  response,
  open,
  onOpenChange,
}: ProvenancePanelProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full gap-0 overflow-y-auto sm:max-w-md"
      >
        {response === null ? null : (
          <>
            <SheetHeader className="gap-1 border-b border-border px-5 pb-4">
              <SheetTitle className="text-base">Sources &amp; details</SheetTitle>
              <SheetDescription className="text-[13px]">
                Where this answer came from and how it was executed.
              </SheetDescription>
            </SheetHeader>

            <div className="space-y-4 px-5 py-4">
              {response.warnings.length > 0 ? (
                <div className="rounded-md border border-warning/40 bg-warning/5 p-3">
                  <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-warning">
                    <AlertTriangle className="size-3.5" aria-hidden="true" />
                    {response.warnings.length === 1 ? "Warning" : "Warnings"}
                  </p>
                  <ul className="space-y-1 text-[13px] text-foreground">
                    {response.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <dl className="divide-y divide-border/50">
                <Field label="Source">
                  {isGovernedMetric(response.provenance.source) ? (
                    <span className="inline-flex items-center gap-1.5">
                      Governed metric
                      <span className="rounded-[3px] bg-success/10 px-1.5 py-0.5 text-[11px] font-medium text-success">
                        certified
                      </span>
                    </span>
                  ) : (
                    <span className="font-mono text-xs">
                      {response.provenance.source}
                    </span>
                  )}
                </Field>

                <Field label="Result">
                  <span className="tnum">
                    {response.execution.row_count}
                    {response.execution.row_count === 1 ? " row" : " rows"} ·{" "}
                    {formatBytes(response.execution.result_bytes)}
                  </span>
                  {response.execution.truncated ? (
                    <span className="ms-1.5 text-warning">(truncated)</span>
                  ) : null}
                </Field>

                <Field label="Duration">
                  <span className="tnum">
                    {formatDuration(response.execution.duration_ms)}
                  </span>
                </Field>

                <Field label="Executed">
                  {formatTimestamp(response.execution.executed_at) ?? "—"}
                </Field>

                <Field label="Freshness">
                  {response.freshness.status === "known" ? (
                    (formatTimestamp(response.freshness.as_of) ?? "Known")
                  ) : (
                    <span className="text-muted-foreground">
                      Not reported by the source
                    </span>
                  )}
                </Field>

                <Field label="Live data">
                  {response.execution.live ? "Yes" : "No"}
                </Field>
              </dl>

              {response.sources.length > 0 ? (
                <Section title="Sources">
                  <div className="flex flex-wrap gap-1.5 py-2">
                    {response.sources.map((source) => (
                      <span
                        key={source}
                        title={source}
                        className="rounded-[3px] border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground"
                      >
                        {source}
                      </span>
                    ))}
                  </div>
                </Section>
              ) : null}

              {response.provenance.tables.length > 0 ? (
                <Section title="Tables read">
                  <div className="flex flex-wrap gap-1.5 py-2">
                    {response.provenance.tables.map((table) => (
                      <span
                        key={table}
                        title={table}
                        className="rounded-[3px] border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground"
                      >
                        {shortTableName(table)}
                      </span>
                    ))}
                  </div>
                </Section>
              ) : null}

              {response.columns.length > 0 ? (
                <Section title="Result columns">
                  <div className="py-2">
                    <table className="w-full text-[12px]">
                      <tbody>
                        {response.columns.map((column) => {
                          const type =
                            response.provenance.result.column_types[column];
                          return (
                            <tr key={column} className="align-baseline">
                              <td className="py-1 pe-3 font-mono text-foreground">
                                {column}
                              </td>
                              <td className="py-1 text-end font-mono text-muted-foreground">
                                {type === undefined || type === "unknown"
                                  ? "—"
                                  : type}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </Section>
              ) : null}

              {/* Present only when the backend explicitly authorised debug output. */}
              {response.provenance.debug !== null ? (
                <Section title="Authorised debug">
                  <Field label="Route">
                    <span className="font-mono text-xs">
                      {response.provenance.debug.route}
                    </span>
                  </Field>
                  {response.provenance.debug.metric_id !== null ? (
                    <Field label="Metric">
                      <span className="font-mono text-xs">
                        {response.provenance.debug.metric_id}
                        {response.provenance.debug.metric_definition_version !==
                        null
                          ? ` v${response.provenance.debug.metric_definition_version}`
                          : ""}
                      </span>
                    </Field>
                  ) : null}
                  {response.provenance.debug.validated_sql !== null ? (
                    <div className="py-2">
                      <p className="mb-1.5 text-xs text-muted-foreground">
                        Validated SQL
                      </p>
                      <pre className="overflow-x-auto rounded-md border border-border bg-muted/50 p-3 font-mono text-[11px] leading-relaxed text-foreground">
                        <code>{response.provenance.debug.validated_sql}</code>
                      </pre>
                    </div>
                  ) : null}
                </Section>
              ) : null}

              <Section title="Request">
                <Field label="Request ID">
                  <span className="wrap-anywhere font-mono text-[11px] text-muted-foreground">
                    {response.request_id}
                  </span>
                </Field>
                <Field label="Thread ID">
                  <span className="wrap-anywhere font-mono text-[11px] text-muted-foreground">
                    {response.thread_id}
                  </span>
                </Field>
              </Section>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
