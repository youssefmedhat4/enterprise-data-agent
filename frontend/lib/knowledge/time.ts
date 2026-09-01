/**
 * Client for the time intelligence API.
 *
 * A calendar is a fact about a company, so it is configuration a reviewer sets:
 * a timezone, a week start, a fiscal start and how fiscal years are named.
 * Nobody writes DATE_TRUNC here — a calendar expressed as SQL is one nothing
 * else can reason about.
 */

import { KnowledgeAccessError } from "@/lib/knowledge/knowledge";

const BASE = "/api/backend/knowledge";

export type WeekStart =
  | "MONDAY"
  | "TUESDAY"
  | "WEDNESDAY"
  | "THURSDAY"
  | "FRIDAY"
  | "SATURDAY"
  | "SUNDAY";

export type FiscalYearLabel = "START_YEAR" | "END_YEAR";
export type PolicyStatus = "DEFAULT" | "CONFIRMED";

export type TemporalRole =
  | "EVENT_TIME"
  | "EFFECTIVE_START"
  | "EFFECTIVE_END"
  | "SNAPSHOT_DATE"
  | "CREATED_AT"
  | "UPDATED_AT"
  | "LOAD_TIME"
  | "START_DATE"
  | "END_DATE";

export type TemporalStorage =
  | "NATIVE_DATE"
  | "NATIVE_TIMESTAMP"
  | "TIMESTAMP_WITH_TIMEZONE"
  | "YYYYMMDD_TEXT";

export interface TimePolicy {
  timezone: string;
  weekStart: WeekStart;
  fiscalYearStartMonth: number;
  fiscalYearStartDay: number;
  fiscalYearLabel: FiscalYearLabel;
  status: PolicyStatus;
  version: number;
  updatedBy: string | null;
  updatedAt: string;
}

export interface TemporalDimension {
  id: string;
  semanticAttributeId: string;
  entity: string;
  concept: string;
  table: string;
  column: string;
  role: TemporalRole;
  storage: TemporalStorage;
  isDefaultForEntity: boolean;
  status: string;
}

export interface TimePreview {
  recognised: boolean;
  label: string;
  timezone: string;
  start: string;
  end: string;
  comparisonLabel: string;
  comparisonStart: string | null;
  comparisonEnd: string | null;
  detail: string;
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    let detail =
      response.status === 403
        ? "Time intelligence requires review authority."
        : "The time intelligence service is unavailable.";
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

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function toPolicy(raw: Record<string, unknown>): TimePolicy {
  return {
    timezone: str(raw.timezone, "UTC"),
    weekStart: str(raw.week_start, "MONDAY") as WeekStart,
    fiscalYearStartMonth: num(raw.fiscal_year_start_month) || 1,
    fiscalYearStartDay: num(raw.fiscal_year_start_day) || 1,
    fiscalYearLabel: str(raw.fiscal_year_label, "START_YEAR") as FiscalYearLabel,
    status: str(raw.status, "DEFAULT") as PolicyStatus,
    version: num(raw.version),
    updatedBy: typeof raw.updated_by === "string" ? raw.updated_by : null,
    updatedAt: str(raw.updated_at),
  };
}

function toDimension(raw: Record<string, unknown>): TemporalDimension {
  return {
    id: str(raw.id),
    semanticAttributeId: str(raw.semantic_attribute_id),
    entity: str(raw.entity),
    concept: str(raw.concept),
    table: str(raw.table),
    column: str(raw.column),
    role: str(raw.role, "EVENT_TIME") as TemporalRole,
    storage: str(raw.storage, "NATIVE_DATE") as TemporalStorage,
    isDefaultForEntity: raw.is_default_for_entity === true,
    status: str(raw.status, "PROPOSED"),
  };
}

export async function fetchTimePolicy(dataSourceId: string): Promise<TimePolicy> {
  return toPolicy(
    (await request(`/data-sources/${dataSourceId}/time-policy`)) as Record<
      string,
      unknown
    >,
  );
}

export async function saveTimePolicy(
  dataSourceId: string,
  policy: TimePolicy,
): Promise<TimePolicy> {
  const payload = await request(`/data-sources/${dataSourceId}/time-policy`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      timezone: policy.timezone,
      week_start: policy.weekStart,
      fiscal_year_start_month: policy.fiscalYearStartMonth,
      fiscal_year_start_day: policy.fiscalYearStartDay,
      fiscal_year_label: policy.fiscalYearLabel,
      status: "CONFIRMED",
    }),
  });
  return toPolicy(payload as Record<string, unknown>);
}

export async function fetchTemporalDimensions(
  dataSourceId: string,
): Promise<TemporalDimension[]> {
  const payload = await request(
    `/data-sources/${dataSourceId}/temporal-dimensions`,
  );
  return Array.isArray(payload)
    ? payload.map((entry) => toDimension(entry as Record<string, unknown>))
    : [];
}

export async function previewTimePhrase(
  dataSourceId: string,
  phrase: string,
): Promise<TimePreview> {
  const raw = (await request(`/data-sources/${dataSourceId}/time-preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phrase }),
  })) as Record<string, unknown>;
  return {
    recognised: raw.recognised === true,
    label: str(raw.label),
    timezone: str(raw.timezone),
    start: str(raw.start),
    end: str(raw.end),
    comparisonLabel: str(raw.comparison_label),
    comparisonStart:
      typeof raw.comparison_start === "string" ? raw.comparison_start : null,
    comparisonEnd:
      typeof raw.comparison_end === "string" ? raw.comparison_end : null,
    detail: str(raw.detail),
  };
}
