/**
 * Client for the data quality API.
 *
 * An assertion names a table and a column and a threshold. It never names a
 * host, a role, or a connection reference, and the backend withholds a custom
 * statement for the same reason it withholds approved example SQL.
 */

import { KnowledgeAccessError } from "@/lib/knowledge/knowledge";

const BASE = "/api/backend/knowledge";

export type AssertionType =
  | "FRESHNESS"
  | "ROW_COUNT"
  | "NULL_RATE"
  | "UNIQUE"
  | "ACCEPTED_VALUES"
  | "CUSTOM_SAFE_SQL";

export type QualityStatus =
  | "HEALTHY"
  | "WARNING"
  | "STALE"
  | "FAILING"
  | "UNKNOWN";

export interface QualityAssertion {
  id: string;
  name: string;
  assertionType: AssertionType;
  table: string;
  columnName: string | null;
  configuration: Record<string, unknown>;
  enabled: boolean;
  status: QualityStatus;
  observed: number | null;
  detail: string | null;
  checkedAt: string | null;
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    let detail =
      response.status === 403
        ? "Data quality requires review authority."
        : "The data quality service is unavailable.";
    if (response.status !== 403) {
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

function toAssertion(raw: Record<string, unknown>): QualityAssertion {
  return {
    id: String(raw.id ?? ""),
    name: String(raw.name ?? ""),
    assertionType: (raw.assertion_type ?? "FRESHNESS") as AssertionType,
    table: String(raw.table ?? ""),
    columnName: typeof raw.column_name === "string" ? raw.column_name : null,
    configuration: (raw.configuration as Record<string, unknown>) ?? {},
    enabled: raw.enabled === true,
    status: (raw.status ?? "UNKNOWN") as QualityStatus,
    observed: typeof raw.observed === "number" ? raw.observed : null,
    detail: typeof raw.detail === "string" ? raw.detail : null,
    checkedAt: typeof raw.checked_at === "string" ? raw.checked_at : null,
  };
}

function toList(payload: unknown): QualityAssertion[] {
  return Array.isArray(payload)
    ? payload.map((entry) => toAssertion(entry as Record<string, unknown>))
    : [];
}

export async function fetchQualityAssertions(
  dataSourceId: string,
): Promise<QualityAssertion[]> {
  return toList(await request(`/data-sources/${dataSourceId}/quality`));
}

export async function runQualityChecks(
  dataSourceId: string,
): Promise<QualityAssertion[]> {
  return toList(
    await request(`/data-sources/${dataSourceId}/quality/run`, { method: "POST" }),
  );
}

export async function toggleQualityAssertion(
  dataSourceId: string,
  assertionId: string,
): Promise<QualityAssertion> {
  const payload = await request(
    `/data-sources/${dataSourceId}/quality/${assertionId}/toggle`,
    { method: "POST" },
  );
  return toAssertion(payload as Record<string, unknown>);
}

export interface NewQualityAssertion {
  name: string;
  assertionType: AssertionType;
  schemaName: string;
  tableName: string;
  columnName?: string | null;
  configuration: Record<string, unknown>;
}

export async function createQualityAssertion(
  dataSourceId: string,
  input: NewQualityAssertion,
): Promise<QualityAssertion> {
  const payload = await request(`/data-sources/${dataSourceId}/quality`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      assertion_type: input.assertionType,
      schema_name: input.schemaName,
      table_name: input.tableName,
      column_name: input.columnName ?? null,
      configuration: input.configuration,
      enabled: true,
    }),
  });
  return toAssertion(payload as Record<string, unknown>);
}
