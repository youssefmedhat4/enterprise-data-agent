/**
 * TypeScript mirror of the backend's public analytics contract.
 *
 * Source of truth: `app/contracts/analytics.py` and `app/errors.py`.
 * These types describe the PUBLIC response only — internal provenance
 * (identity, policy, model routing, timings) never crosses the API boundary.
 *
 * Do not add fields here speculatively. If a field is not in the Pydantic
 * model, it does not exist.
 */

/** `type Scalar = str | int | float | bool | None` */
export type Scalar = string | number | boolean | null;

/** A result row is keyed by column name. Values are JSON scalars. */
export type ResultRow = Record<string, Scalar>;

export type ModelProfile = "gemini_pro" | "gemini";

/** `AnalyticsRequest` — the body is `extra="forbid"`; unknown keys are a 422. */
export interface AnalyticsRequest {
  question: string;
  thread_id?: string | null;
  include_debug?: boolean;
  model_profile?: ModelProfile;
}

/**
 * `ChartSpec` — the AI-selected, backend-validated visualization contract.
 *
 * The Pydantic field is `chart_type` but serialises under the alias `type`,
 * which is what appears on the wire. Every field here is data, never code: the
 * backend contract has no field capable of carrying an executable payload, and
 * `ChartValidator` has already confirmed that every column named below exists in
 * `rows` and holds the right kind of value. The renderer can therefore treat
 * this spec as trustworthy without re-deriving anything.
 *
 * Multi-series arrives one of two ways, never both at once: long format
 * (`series` names a grouping column, one entry in `measures`) or wide format
 * (several `measures`, `series` null).
 */
export type ChartType = "bar" | "line" | "area" | "pie" | "donut" | "scatter";

/** How a measure column is stored — never anything derived from it. */
export type MeasureFormat = "number" | "currency" | "percent";

/** Slice labelling for pie/donut. Inert for every other chart type. */
export type PartToWholeDisplay = "value" | "percent" | "value_and_percent";

export interface ChartSpec {
  type: ChartType;
  title: string;
  /** Category, temporal, or (for scatter) numeric column for the x axis. */
  x: string;
  /** One or more numeric result columns to plot. Always at least one. */
  measures: string[];
  /** Long-format grouping column, or null. */
  series: string | null;
  /** Honoured for bar charts; normalised to "vertical" for other types. */
  orientation: "vertical" | "horizontal";
  /** How multiple series combine. Only meaningful for bar and area. */
  mode: "grouped" | "stacked";
  x_label: string | null;
  y_label: string | null;
  /**
   * How the measure column is stored. A question about "share" does not make an
   * amount column a percentage — that is what `part_to_whole_display` is for.
   */
  value_format: MeasureFormat;
  /**
   * Pie/donut slice labelling. The share is derived at render time from the
   * plotted values; it is never a column in `rows` and never enters grounding.
   */
  part_to_whole_display: PartToWholeDisplay;
  /** Display-only reordering by the first measure. Values are never altered. */
  sort: "none" | "ascending" | "descending";
  /** Display-only cap on rendered categories. */
  limit: number | null;
}

export interface Freshness {
  status: "known" | "unknown";
  as_of: string | null;
}

export interface ResultMetadata {
  row_count: number;
  columns: string[];
  /** Column name -> physical type (`text`, `int8`, `numeric`, ...). May be `unknown`. */
  column_types: Record<string, string>;
  result_bytes: number;
  truncated: boolean;
  live: boolean;
}

/**
 * `DebugProvenance` — only ever populated when all three hold: the request
 * asked for it, `API_DEBUG_PROVENANCE_ENABLED=1`, and policy grants `debug`.
 * A `null` here is the normal case, not an error.
 */
export interface DebugProvenance {
  generated_sql: string | null;
  validated_sql: string | null;
  selected_schema_ids: string[];
  semantic_definition_ids: string[];
  semantic_provider: string;
  semantic_retrieval_latency_ms: number;
  semantic_model_ids: string[];
  semantic_relationship_ids: string[];
  semantic_measure_ids: string[];
  sql_generation_provider: string;
  route: string;
  route_reason_code: string;
  route_confidence: number;
  metric_id: string | null;
  metric_definition_version: string | null;
  metric_dimensions: string[];
  metric_filters: Record<string, unknown>[];
  metric_provider: string | null;
  execution_source: string;
  routing_latency_ms: number;
  metric_planning_latency_ms: number;
  metric_retrieval_latency_ms: number;
  metric_execution_latency_ms: number;
  sql_validation_attempts: number;
  sql_repair_attempted: boolean;
  sql_repair_succeeded: boolean;
  initial_validation_error_code: string | null;
  final_validation_status: string;
  repair_latency_ms: number;
  sql_parse_latency_ms: number;
  sql_schema_validation_latency_ms: number;
  original_candidate_sql: string | null;
  repaired_candidate_sql: string | null;
}

/** Public `Provenance`. Deliberately small. */
export interface Provenance {
  source: string;
  tables: string[];
  columns: string[];
  result: ResultMetadata;
  executed_at: string | null;
  freshness: Freshness;
  debug: DebugProvenance | null;
}

export type AnalyticsStatus =
  | "completed"
  | "clarification_required"
  | "blocked"
  | "empty";

export interface ExecutionMetadata {
  query_id: string | null;
  status: AnalyticsStatus;
  row_count: number;
  duration_ms: number;
  executed_at: string | null;
  result_bytes: number;
  truncated: boolean;
  live: boolean;
}

/** `AnalyticsResponse`, schema version 1.0. */
export interface AnalyticsResponse {
  schema_version: "1.1";
  request_id: string;
  thread_id: string;
  model_profile: ModelProfile;
  model_display_name: string;
  status: AnalyticsStatus;
  answer: string;
  columns: string[];
  rows: ResultRow[];
  chart: ChartSpec | null;
  sources: string[];
  provenance: Provenance;
  freshness: Freshness;
  clarification_required: boolean;
  clarification_question: string | null;
  warnings: string[];
  execution: ExecutionMetadata;
}

/** Stable error codes from `app/errors.py::ErrorCode`. */
export type ErrorCode =
  | "invalid_request"
  | "authentication_failed"
  | "authentication_unavailable"
  | "authorization_denied"
  | "authorization_unavailable"
  | "checkpoint_unavailable"
  | "governance_provider_unavailable"
  | "clarification_required"
  | "unsafe_sql"
  | "sql_validation_failed"
  | "sql_schema_validation_failed"
  | "sql_repair_failed"
  | "database_unavailable"
  | "database_configuration_error"
  | "database_permission_denied"
  | "query_execution_failed"
  | "result_too_large"
  | "query_timeout"
  | "llm_unavailable"
  | "llm_rate_limited"
  | "invalid_structured_model_output"
  | "grounding_failure"
  | "semantic_provider_unavailable"
  | "metric_provider_unavailable"
  | "invalid_metric_query"
  | "router_failure"
  | "metric_planning_failure"
  | "internal_unexpected_error";

export interface ErrorDetail {
  code: ErrorCode;
  message: string;
  request_id: string;
  retryable: boolean;
}

/** Every non-2xx response from the API uses this envelope. */
export interface ErrorResponse {
  error: ErrorDetail;
}

/** `HealthResponse` for `/health`, `/health/live` and `/health/ready`. */
export interface HealthResponse {
  status: "ok" | "ready";
  checks: Record<string, "ok" | "skipped">;
}
