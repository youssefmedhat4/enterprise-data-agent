/**
 * Client for the evaluation API.
 *
 * An evaluation set is the questions whose right answers are already known, so
 * a change that breaks one is caught by running them rather than by a user
 * finding out. Everything here needs review authority, which the backend checks.
 */

import { KnowledgeAccessError } from "@/lib/knowledge/knowledge";

const BASE = "/api/backend/knowledge";

export type Expectation = "SCALAR" | "TABLE" | "ROW_COUNT" | "EMPTY";
export type CaseOutcome = "PASS" | "FAIL" | "ERROR" | "SKIPPED";
export type Movement =
  | "UNCHANGED_PASS"
  | "UNCHANGED_FAIL"
  | "IMPROVED"
  | "REGRESSION"
  | "NEW";

export interface EvaluationCase {
  id: string;
  name: string;
  question: string;
  expectation: Expectation;
  expected: Record<string, unknown>;
  tolerance: string;
  ordered: boolean;
  expectedRoute: string | null;
  status: "ACTIVE" | "ARCHIVED";
  createdAt: string;
}

export interface CaseResult {
  caseId: string;
  name: string;
  question: string;
  expected: string;
  outcome: CaseOutcome;
  movement: Movement;
  actual: string | null;
  detail: string | null;
  route: string | null;
  latencyMs: number;
}

export interface EvaluationRun {
  id: string;
  modelProfile: string;
  startedAt: string;
  finishedAt: string | null;
  caseCount: number;
  passed: number;
  failed: number;
  errored: number;
  passRate: number;
  averageLatencyMs: number;
  regressions: number;
  improvements: number;
  results: CaseResult[];
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    let detail = "The evaluation service is unavailable.";
    if (response.status === 403) {
      detail = "Evaluations require review authority.";
    } else {
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
        // Keep the generic message.
      }
    }
    throw new KnowledgeAccessError(response.status, detail);
  }
  return response.json();
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function toCase(raw: Record<string, unknown>): EvaluationCase {
  return {
    id: str(raw.id),
    name: str(raw.name),
    question: str(raw.question),
    expectation: str(raw.expectation, "SCALAR") as Expectation,
    expected: (raw.expected as Record<string, unknown>) ?? {},
    tolerance: str(raw.tolerance, "0"),
    ordered: raw.ordered === true,
    expectedRoute: typeof raw.expected_route === "string" ? raw.expected_route : null,
    status: str(raw.status, "ACTIVE") as "ACTIVE" | "ARCHIVED",
    createdAt: str(raw.created_at),
  };
}

function toRun(raw: Record<string, unknown>): EvaluationRun {
  const results = Array.isArray(raw.results) ? raw.results : [];
  return {
    id: str(raw.id),
    modelProfile: str(raw.model_profile),
    startedAt: str(raw.started_at),
    finishedAt: typeof raw.finished_at === "string" ? raw.finished_at : null,
    caseCount: num(raw.case_count),
    passed: num(raw.passed),
    failed: num(raw.failed),
    errored: num(raw.errored),
    passRate: num(raw.pass_rate),
    averageLatencyMs: num(raw.average_latency_ms),
    regressions: num(raw.regressions),
    improvements: num(raw.improvements),
    results: results.map((entry) => {
      const row = entry as Record<string, unknown>;
      return {
        caseId: str(row.case_id),
        name: str(row.name),
        question: str(row.question),
        expected: str(row.expected),
        outcome: str(row.outcome, "ERROR") as CaseOutcome,
        movement: str(row.movement, "NEW") as Movement,
        actual: typeof row.actual === "string" ? row.actual : null,
        detail: typeof row.detail === "string" ? row.detail : null,
        route: typeof row.route === "string" ? row.route : null,
        latencyMs: num(row.latency_ms),
      };
    }),
  };
}

export async function fetchEvaluationCases(
  dataSourceId: string,
): Promise<EvaluationCase[]> {
  const payload = await request(`/data-sources/${dataSourceId}/evaluation-cases`);
  return Array.isArray(payload)
    ? payload.map((entry) => toCase(entry as Record<string, unknown>))
    : [];
}

export async function fetchEvaluationRuns(
  dataSourceId: string,
): Promise<EvaluationRun[]> {
  const payload = await request(`/data-sources/${dataSourceId}/evaluation-runs`);
  return Array.isArray(payload)
    ? payload.map((entry) => toRun(entry as Record<string, unknown>))
    : [];
}

/** Runs every active case. Deliberately manual: each one costs a model call. */
export async function runEvaluation(dataSourceId: string): Promise<EvaluationRun> {
  const payload = await request(`/data-sources/${dataSourceId}/evaluation-runs`, {
    method: "POST",
  });
  return toRun(payload as Record<string, unknown>);
}

export interface NewEvaluationCase {
  name: string;
  question: string;
  expectation: Expectation;
  expected: Record<string, unknown>;
  tolerance?: string;
  ordered?: boolean;
  expectedRoute?: string | null;
}

export async function createEvaluationCase(
  dataSourceId: string,
  input: NewEvaluationCase,
): Promise<EvaluationCase> {
  const payload = await request(`/data-sources/${dataSourceId}/evaluation-cases`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      question: input.question,
      expectation: input.expectation,
      expected: input.expected,
      tolerance: input.tolerance ?? "0",
      ordered: input.ordered ?? false,
      expected_route: input.expectedRoute ?? null,
    }),
  });
  return toCase(payload as Record<string, unknown>);
}

export async function archiveEvaluationCase(
  dataSourceId: string,
  item: EvaluationCase,
): Promise<EvaluationCase> {
  const payload = await request(
    `/data-sources/${dataSourceId}/evaluation-cases/${item.id}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: item.name,
        question: item.question,
        expectation: item.expectation,
        expected: item.expected,
        tolerance: item.tolerance,
        ordered: item.ordered,
        expected_route: item.expectedRoute,
        status: item.status === "ACTIVE" ? "ARCHIVED" : "ACTIVE",
      }),
    },
  );
  return toCase(payload as Record<string, unknown>);
}
