"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ClipboardList, Play, TrendingDown, TrendingUp } from "lucide-react";

import { Badge } from "@/components/ui/badge";
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

/**
 * Known-answer questions, and whether they still answer correctly.
 *
 * Deliberately quiet: the only thing worth noticing on this page is a
 * regression, so nothing else competes for attention. A run is started by a
 * person, never on load — every case costs a model call.
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
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
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
        <p className="rounded-lg border border-border p-3 text-sm text-muted-foreground">
          {error}
        </p>
      ) : null}

      {latest !== null ? (
        <>
          {latest.regressions > 0 ? (
            <p className="flex items-center gap-2 rounded-lg border border-warning/40 bg-warning/5 p-3 text-sm">
              <TrendingDown className="size-4 shrink-0 text-warning" aria-hidden="true" />
              <span>
                {latest.regressions} question
                {latest.regressions === 1 ? "" : "s"} that passed last time now fail.
              </span>
            </p>
          ) : null}

          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Pass rate" value={`${Math.round(latest.passRate * 100)}%`} />
            <Stat label="Questions" value={String(latest.caseCount)} />
            <Stat label="Regressions" value={String(latest.regressions)} />
            <Stat
              label="Avg latency"
              value={`${(latest.averageLatencyMs / 1000).toFixed(1)}s`}
            />
          </dl>
          <p className="text-xs text-muted-foreground">
            Last run {new Date(latest.startedAt).toLocaleString()} · {latest.modelProfile}
            {latest.improvements > 0 ? (
              <span className="ms-2 inline-flex items-center gap-1">
                <TrendingUp className="size-3" aria-hidden="true" />
                {latest.improvements} improved
              </span>
            ) : null}
          </p>

          <div className="flex flex-wrap gap-1.5">
            {(["ALL", "REGRESSION", "FAIL", "ERROR", "PASS"] as Filter[]).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setFilter(option)}
                className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                  filter === option
                    ? "border-foreground/30 bg-muted"
                    : "border-border text-muted-foreground hover:bg-muted/60"
                }`}
              >
                {option === "ALL" ? "All" : option.toLowerCase()}
              </button>
            ))}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[42rem] text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="py-2 pe-3 font-medium">Question</th>
                  <th className="py-2 pe-3 font-medium">Expected</th>
                  <th className="py-2 pe-3 font-medium">Actual</th>
                  <th className="py-2 pe-3 font-medium">Route</th>
                  <th className="py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {results.map((result) => (
                  <tr key={result.caseId} className="border-t border-border/60 align-top">
                    <td className="py-2 pe-3">
                      <span className="font-medium">{result.name}</span>
                      <span className="block text-xs text-muted-foreground">
                        {result.question}
                      </span>
                      {result.detail !== null ? (
                        <span className="mt-1 block text-xs text-muted-foreground">
                          {result.detail}
                        </span>
                      ) : null}
                    </td>
                    <td className="py-2 pe-3 font-mono text-xs">{result.expected}</td>
                    <td className="py-2 pe-3 font-mono text-xs">{result.actual ?? "—"}</td>
                    <td className="py-2 pe-3 text-xs text-muted-foreground">
                      {result.route ?? "—"}
                    </td>
                    <td className="py-2">
                      <Badge
                        variant={
                          result.movement === "REGRESSION" || result.outcome === "FAIL"
                            ? "destructive"
                            : result.outcome === "ERROR"
                              ? "secondary"
                              : "default"
                        }
                      >
                        {result.movement === "REGRESSION"
                          ? MOVEMENT_LABEL.REGRESSION
                          : result.outcome}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {results.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                Nothing matches this filter.
              </p>
            ) : null}
          </div>
        </>
      ) : (
        <p className="flex items-center gap-2 rounded-lg border border-border p-4 text-sm text-muted-foreground">
          <ClipboardList className="size-4" aria-hidden="true" />
          No runs yet for this database.
        </p>
      )}

      {cases.length > 0 ? (
        <details className="rounded-lg border border-border p-4">
          <summary className="cursor-pointer text-sm font-medium">
            Evaluation set ({cases.length})
          </summary>
          <ul className="mt-3 space-y-2">
            {cases.map((item) => (
              <li key={item.id} className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium">{item.name}</p>
                  <p className="text-xs text-muted-foreground">{item.question}</p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void toggleArchive(item)}
                >
                  Archive
                </Button>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function filtered(results: CaseResult[], filter: Filter): CaseResult[] {
  if (filter === "ALL") return results;
  if (filter === "REGRESSION") {
    return results.filter((result) => result.movement === "REGRESSION");
  }
  return results.filter((result) => result.outcome === filter);
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-lg font-medium tabular-nums">{value}</dd>
    </div>
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
