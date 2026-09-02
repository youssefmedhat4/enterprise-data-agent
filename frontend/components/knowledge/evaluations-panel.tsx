"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  ClipboardList,
  Play,
  TrendingDown,
  TrendingUp,
} from "lucide-react";

import {
  EmptyState,
  Panel,
  StatusBadge,
  type StatusTone,
} from "@/components/knowledge/shell/primitives";
import { Button } from "@/components/ui/button";
import {
  archiveEvaluationCase,
  fetchEvaluationCases,
  fetchEvaluationRuns,
  runEvaluation,
  type CaseResult,
  type EvaluationCase,
  type EvaluationRun,
  type Movement,
} from "@/lib/knowledge/evaluation";
import { KnowledgeAccessError } from "@/lib/knowledge/knowledge";
import { cn } from "@/lib/utils";

/**
 * Known-answer questions, and whether they still answer correctly.
 *
 * Deliberately quiet: the only thing worth noticing on this page is a
 * regression, so nothing else competes for attention — the pass rate is stated
 * plainly, and colour is spent only where something moved the wrong way. A run
 * is started by a person, never on load: every case costs a model call.
 */
interface EvaluationsPanelProps {
  dataSourceId: string;
}

const MOVEMENT_LABEL: Record<Movement, string> = {
  REGRESSION: "Regression",
  IMPROVED: "Improved",
  NEW: "New",
  UNCHANGED_PASS: "Unchanged",
  UNCHANGED_FAIL: "Still failing",
};

type Filter = "ALL" | "PASS" | "FAIL" | "REGRESSION" | "ERROR";

const FILTER_LABEL: Record<Filter, string> = {
  ALL: "All",
  REGRESSION: "Regressions",
  FAIL: "Failing",
  ERROR: "Errored",
  PASS: "Passing",
};

export function EvaluationsPanel({ dataSourceId }: EvaluationsPanelProps) {
  const [cases, setCases] = useState<EvaluationCase[]>([]);
  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [filter, setFilter] = useState<Filter>("ALL");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextCases, nextRuns] = await Promise.all([
        fetchEvaluationCases(dataSourceId),
        fetchEvaluationRuns(dataSourceId),
      ]);
      setCases(nextCases);
      setRuns(nextRuns);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof KnowledgeAccessError
          ? caught.message
          : "The evaluation service is unavailable.",
      );
    }
  }, [dataSourceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await runEvaluation(dataSourceId);
      await load();
    } catch (caught) {
      setError(
        caught instanceof KnowledgeAccessError
          ? caught.message
          : "The evaluation run could not be started.",
      );
    } finally {
      setBusy(false);
    }
  };

  const toggleArchive = async (item: EvaluationCase) => {
    setBusy(true);
    try {
      await archiveEvaluationCase(dataSourceId, item);
      await load();
    } catch {
      setError("The case could not be updated.");
    } finally {
      setBusy(false);
    }
  };

  const latest = runs[0] ?? null;
  const results = latest ? filtered(latest.results, filter) : [];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[13px] text-muted-foreground">
          {cases.length === 0
            ? "No evaluation cases yet. Add one from a completed analysis."
            : `${cases.length} question${cases.length === 1 ? "" : "s"} with a known answer.`}
        </p>
        <Button size="sm" disabled={busy || cases.length === 0} onClick={() => void start()}>
          <Play className="size-3.5" aria-hidden="true" />
          {busy ? "Running…" : "Run evaluation"}
        </Button>
      </div>

      {error !== null ? (
        <Panel className="px-4 py-3 text-[13px] text-muted-foreground">
          {error}
        </Panel>
      ) : null}

      {latest !== null ? (
        <>
          {/* The one thing on this page that should interrupt a day. */}
          {latest.regressions > 0 ? (
            <div className="flex items-start gap-2.5 rounded-xl border border-destructive/30 bg-destructive/8 px-4 py-3">
              <TrendingDown
                className="mt-0.5 size-4 shrink-0 text-destructive"
                aria-hidden="true"
              />
              <p className="text-[13px] leading-relaxed text-foreground">
                <span className="font-medium">
                  {latest.regressions} question
                  {latest.regressions === 1 ? "" : "s"} that passed last time now
                  fail.
                </span>{" "}
                <button
                  type="button"
                  onClick={() => setFilter("REGRESSION")}
                  className="rounded-md text-destructive underline-offset-4 outline-none transition-colors hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
                >
                  Show them
                </button>
              </p>
            </div>
          ) : null}

          <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <RunStat
              label="Pass rate"
              value={`${Math.round(latest.passRate * 100)}%`}
              tone={latest.passRate >= 0.999 ? "positive" : "neutral"}
            />
            <RunStat label="Questions" value={String(latest.caseCount)} />
            <RunStat
              label="Regressions"
              value={String(latest.regressions)}
              tone={latest.regressions > 0 ? "critical" : "neutral"}
            />
            <RunStat
              label="Avg latency"
              value={`${(latest.averageLatencyMs / 1000).toFixed(1)}s`}
            />
          </dl>

          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted-foreground">
            <span>Last run {new Date(latest.startedAt).toLocaleString()}</span>
            <span aria-hidden="true">·</span>
            <span className="font-mono text-[11.5px]">{latest.modelProfile}</span>
            {latest.improvements > 0 ? (
              <span className="inline-flex items-center gap-1 text-success">
                <TrendingUp className="size-3.5" aria-hidden="true" />
                {latest.improvements} improved
              </span>
            ) : null}
          </p>

          <div
            role="group"
            aria-label="Filter results"
            className="flex flex-wrap gap-1 rounded-lg border border-hairline bg-surface p-1"
          >
            {(["ALL", "REGRESSION", "FAIL", "ERROR", "PASS"] as Filter[]).map(
              (option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={filter === option}
                  onClick={() => setFilter(option)}
                  className={cn(
                    "rounded-md px-2.5 py-1 text-[12.5px] outline-none transition-colors",
                    "focus-visible:ring-[3px] focus-visible:ring-ring/50",
                    filter === option
                      ? "bg-surface-raised font-medium text-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {FILTER_LABEL[option]}
                </button>
              ),
            )}
          </div>

          <Panel className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[46rem] text-[13px]">
                <thead>
                  <tr className="border-b border-hairline text-left">
                    {["Question", "Expected", "Actual", "Route", "Status"].map(
                      (heading) => (
                        <th
                          key={heading}
                          className="label-xs px-4 py-3 text-muted-foreground"
                        >
                          {heading}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {results.map((result) => (
                    <tr
                      key={result.caseId}
                      className="border-b border-hairline align-top transition-colors last:border-0 hover:bg-surface-raised/60"
                    >
                      <td className="px-4 py-3">
                        <span className="font-medium text-foreground">
                          {result.name}
                        </span>
                        <span className="mt-0.5 block text-[12.5px] text-muted-foreground">
                          {result.question}
                        </span>
                        {result.detail !== null ? (
                          <span className="mt-1 block text-[12.5px] text-muted-foreground">
                            {result.detail}
                          </span>
                        ) : null}
                      </td>
                      <td className="px-4 py-3 font-mono text-[12px] text-muted-foreground">
                        {result.expected}
                      </td>
                      <td className="px-4 py-3 font-mono text-[12px] text-foreground">
                        {result.actual ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-[12.5px] text-muted-foreground">
                        {result.route ?? "—"}
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge tone={outcomeTone(result)} dot>
                          {result.movement === "REGRESSION"
                            ? MOVEMENT_LABEL.REGRESSION
                            : result.outcome}
                        </StatusBadge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {results.length === 0 ? (
              <p className="px-4 py-10 text-center text-[13px] text-muted-foreground">
                Nothing matches this filter.
              </p>
            ) : null}
          </Panel>
        </>
      ) : (
        <EmptyState
          icon={ClipboardList}
          title="No runs yet for this database"
          description="A run answers every known-answer question and compares the result. It is started by a person because each case costs a model call."
        />
      )}

      {cases.length > 0 ? (
        <Panel asChild>
          <details className="group/set p-5">
            <summary className="cursor-pointer text-[13.5px] font-medium text-foreground outline-none transition-colors hover:text-foreground/80 focus-visible:ring-[3px] focus-visible:ring-ring/50">
              Evaluation set ({cases.length})
            </summary>
            <ul className="mt-4 divide-y divide-hairline">
              {cases.map((item) => (
                <li
                  key={item.id}
                  className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0"
                >
                  <div className="min-w-0">
                    <p className="text-[13.5px] font-medium text-foreground">
                      {item.name}
                    </p>
                    <p className="text-[12.5px] text-muted-foreground">
                      {item.question}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => void toggleArchive(item)}
                  >
                    Archive
                  </Button>
                </li>
              ))}
            </ul>
          </details>
        </Panel>
      ) : null}
    </div>
  );
}

function outcomeTone(result: CaseResult): StatusTone {
  if (result.movement === "REGRESSION" || result.outcome === "FAIL") {
    return "critical";
  }
  if (result.outcome === "ERROR") return "attention";
  return "positive";
}

function filtered(results: CaseResult[], filter: Filter): CaseResult[] {
  if (filter === "ALL") return results;
  if (filter === "REGRESSION") {
    return results.filter((result) => result.movement === "REGRESSION");
  }
  return results.filter((result) => result.outcome === filter);
}

function RunStat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: StatusTone;
}) {
  return (
    <Panel className="px-4 py-3.5">
      <dt className="text-[12.5px] text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "mt-1.5 text-[24px] font-semibold leading-none tracking-tight tabular-nums",
          tone === "critical"
            ? "text-destructive"
            : tone === "positive"
              ? "text-success"
              : "text-foreground",
        )}
      >
        {value}
      </dd>
    </Panel>
  );
}

export function RegressionWarning({ run }: { run: EvaluationRun | null }) {
  if (run === null || run.regressions === 0) return null;
  return (
    <span className="inline-flex items-center gap-1 text-warning">
      <AlertTriangle className="size-3.5" aria-hidden="true" />
      {run.regressions}
    </span>
  );
}
