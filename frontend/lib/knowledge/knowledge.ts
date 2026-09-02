/**
 * Client for the knowledge administration API.
 *
 * Every call goes through the same-origin proxy, and every route behind it
 * requires review authority the backend checks. A 403 here is a normal outcome
 * for an ordinary analyst, not an error to retry.
 */

const BASE = "/api/backend/knowledge";

export type ApprovalStatus = "PROPOSED" | "CONFIRMED" | "REJECTED" | "STALE";
export type CandidateStatus = "PROPOSED" | "APPROVED" | "REJECTED" | "STALE";

export interface KnowledgeCluster {
  id: string;
  canonicalSummary: string;
  structuralFingerprint: string;
  occurrenceCount: number;
  successfulCount: number;
  firstSeenAt: string;
  lastSeenAt: string;
  status: string;
  candidateId: string | null;
  candidateStatus: string | null;
  promotedToType: string | null;
  promotedToId: string | null;
}

export interface KnowledgeCandidate {
  id: string;
  candidateType: string;
  displayName: string;
  description: string;
  status: CandidateStatus;
  evidenceCount: number;
  successfulEvidenceCount: number;
  expression: string | null;
  grain: string | null;
  dependencies: string[];
  rejectionReason: string | null;
  clusterId: string | null;
  promotedToType: string | null;
  promotedToId: string | null;
  /** Type-specific facts a reviewer needs in order to decide. */
  detail: { label: string; value: string }[];
}

export interface CertifiedMetric {
  id: string;
  metricKey: string;
  displayName: string;
  description: string;
  businessMeaning: string;
  version: number;
  status: string;
  grain: string | null;
  unit: string | null;
  dimensions: string[];
  dependencies: string[];
  semanticExpression: string | null;
  approvedAt: string | null;
  approvedBy: string | null;
  sourceCandidateId: string | null;
}

export interface QueryExample {
  id: string;
  question: string;
  semanticPlan: string;
  status: ApprovalStatus;
  schemaFingerprint: string | null;
  approvedAt: string | null;
  sourceCandidateId: string | null;
  sourceClusterId: string | null;
  approvedBy: string | null;
}

export interface BusinessInstruction {
  id: string;
  title: string;
  instruction: string;
  semanticConcepts: string[];
  metricKeys: string[];
  status: ApprovalStatus;
  sourceCandidateId: string | null;
  approvedAt: string | null;
  approvedBy: string | null;
}

export class KnowledgeAccessError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "KnowledgeAccessError";
    this.status = status;
  }
}

async function get<T>(path: string, map: (raw: never) => T): Promise<T[]> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new KnowledgeAccessError(
      response.status,
      response.status === 403
        ? "Knowledge administration requires review authority."
        : "The knowledge service is unavailable.",
    );
  }
  const payload: unknown = await response.json();
  return Array.isArray(payload) ? payload.map((entry) => map(entry as never)) : [];
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function nullable(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function fetchClusters(dataSourceId: string): Promise<KnowledgeCluster[]> {
  return get(`/data-sources/${dataSourceId}/clusters`, (raw: Record<string, unknown>) => ({
    id: str(raw.id),
    canonicalSummary: str(raw.canonical_summary),
    structuralFingerprint: str(raw.structural_fingerprint),
    occurrenceCount: num(raw.occurrence_count),
    successfulCount: num(raw.successful_count),
    firstSeenAt: str(raw.first_seen_at),
    lastSeenAt: str(raw.last_seen_at),
    status: str(raw.status, "ACTIVE"),
    candidateId: nullable(raw.candidate_id),
    candidateStatus: nullable(raw.candidate_status),
    promotedToType: nullable(raw.promoted_to_type),
    promotedToId: nullable(raw.promoted_to_id),
  }));
}

export function fetchCandidates(dataSourceId: string): Promise<KnowledgeCandidate[]> {
  return get(`/data-sources/${dataSourceId}/candidates`, (raw: Record<string, unknown>) => ({
    id: str(raw.id),
    candidateType: str(raw.candidate_type, "METRIC"),
    displayName: str(raw.display_name),
    description: str(raw.description),
    status: str(raw.status, "PROPOSED") as CandidateStatus,
    evidenceCount: num(raw.evidence_count),
    successfulEvidenceCount: num(raw.successful_evidence_count),
    expression: nullable(raw.expression),
    grain: nullable(raw.grain),
    dependencies: strList(raw.dependencies),
    rejectionReason: nullable(raw.rejection_reason),
    clusterId: nullable(raw.cluster_id),
    promotedToType: nullable(raw.promoted_to_type),
    promotedToId: nullable(raw.promoted_to_id),
    detail: Array.isArray(raw.detail)
      ? raw.detail.map((entry) => {
          const row = entry as Record<string, unknown>;
          return { label: str(row.label), value: str(row.value) };
        })
      : [],
  }));
}

export function fetchCertifiedMetrics(dataSourceId: string): Promise<CertifiedMetric[]> {
  return get(`/data-sources/${dataSourceId}/metrics`, (raw: Record<string, unknown>) => ({
    id: str(raw.id),
    metricKey: str(raw.metric_key),
    displayName: str(raw.display_name),
    description: str(raw.description),
    businessMeaning: str(raw.business_meaning),
    version: num(raw.version),
    status: str(raw.status, "CERTIFIED"),
    grain: nullable(raw.grain),
    unit: nullable(raw.unit),
    dimensions: strList(raw.dimensions),
    dependencies: strList(raw.dependencies),
    semanticExpression: nullable(raw.semantic_expression),
    approvedAt: nullable(raw.approved_at),
    approvedBy: nullable(raw.approved_by),
    sourceCandidateId: nullable(raw.source_candidate_id),
  }));
}

export function fetchQueryExamples(dataSourceId: string): Promise<QueryExample[]> {
  return get(`/data-sources/${dataSourceId}/examples`, (raw: Record<string, unknown>) => ({
    id: str(raw.id),
    question: str(raw.question),
    semanticPlan: str(raw.semantic_plan),
    status: str(raw.status, "PROPOSED") as ApprovalStatus,
    schemaFingerprint: nullable(raw.schema_fingerprint),
    approvedAt: nullable(raw.approved_at),
    sourceCandidateId: nullable(raw.source_candidate_id),
    sourceClusterId: nullable(raw.source_cluster_id),
    approvedBy: nullable(raw.approved_by),
  }));
}

export function fetchBusinessInstructions(
  dataSourceId: string,
): Promise<BusinessInstruction[]> {
  return get(`/data-sources/${dataSourceId}/instructions`, (raw: Record<string, unknown>) => ({
    id: str(raw.id),
    title: str(raw.title),
    instruction: str(raw.instruction),
    semanticConcepts: strList(raw.semantic_concepts),
    metricKeys: strList(raw.metric_keys),
    status: str(raw.status, "CONFIRMED") as ApprovalStatus,
    sourceCandidateId: nullable(raw.source_candidate_id),
    approvedAt: nullable(raw.approved_at),
    approvedBy: nullable(raw.approved_by),
  }));
}

/**
 * Approve or reject a candidate.
 *
 * The decision is made by the backend, which re-validates dependencies, grain
 * and expression before certifying anything. A rejection here is a real answer,
 * not a client-side status change.
 */
export async function reviewCandidate(
  dataSourceId: string,
  candidateId: string,
  action: "approve" | "reject",
  reason?: string,
): Promise<{ ok: boolean; message: string }> {
  const response = await fetch(
    `${BASE}/data-sources/${dataSourceId}/candidates/${candidateId}/review`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ action, reason: reason ?? null }),
    },
  );
  const payload: unknown = await response.json().catch(() => null);
  if (response.ok) {
    return { ok: true, message: action === "approve" ? "Certified." : "Rejected." };
  }
  const detail =
    payload !== null && typeof payload === "object" && "detail" in payload
      ? String((payload as { detail: unknown }).detail)
      : "The review could not be completed.";
  return { ok: false, message: detail };
}

export interface SemanticProposal {
  id: string;
  kind: "entity" | "attribute" | "relationship";
  /** Fully qualified `schema.table.column`, kept for the technical view. */
  physical: string;
  proposedConcept: string;
  confidence: number | null;
  status: ApprovalStatus;
  detail: string;
  /** The concept this sits under: an entity names itself, an attribute its owner. */
  entityName: string | null;
  schemaName: string | null;
  tableName: string | null;
  columnName: string | null;
  dataType: string | null;
  /** The attribute that distinguishes one instance of the entity from another. */
  isIdentifier: boolean;
  fromEntity: string | null;
  toEntity: string | null;
}

function optional(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

export function fetchSemantics(dataSourceId: string): Promise<SemanticProposal[]> {
  return get(`/data-sources/${dataSourceId}/semantics`, (raw: Record<string, unknown>) => ({
    id: str(raw.id),
    kind: str(raw.kind, "entity") as SemanticProposal["kind"],
    physical: str(raw.physical),
    proposedConcept: str(raw.proposed_concept),
    confidence: typeof raw.confidence === "number" ? raw.confidence : null,
    status: str(raw.status, "PROPOSED") as ApprovalStatus,
    detail: str(raw.detail),
    entityName: optional(raw.entity_name),
    schemaName: optional(raw.schema_name),
    tableName: optional(raw.table_name),
    columnName: optional(raw.column_name),
    dataType: optional(raw.data_type),
    isIdentifier: raw.is_identifier === true,
    fromEntity: optional(raw.from_entity),
    toEntity: optional(raw.to_entity),
  }));
}

/**
 * A few example values per column, keyed by `schema.table.column`.
 *
 * Strictly an aid to reading a column, and entirely optional: the backend
 * returns nothing for a column its discovery limits exclude, and a datasource
 * that cannot be reached simply yields no previews rather than an error the
 * reviewer has to dismiss before they can work.
 */
export async function fetchColumnPreviews(
  dataSourceId: string,
): Promise<Record<string, string[]>> {
  try {
    const rows = await get(
      `/data-sources/${dataSourceId}/column-previews`,
      (raw: Record<string, unknown>) => ({
        column: str(raw.column),
        values: Array.isArray(raw.values)
          ? raw.values.map((item) => str(item)).filter(Boolean).slice(0, 5)
          : [],
      }),
    );
    return Object.fromEntries(
      rows
        .filter((row) => row.column !== "" && row.values.length > 0)
        .map((row) => [row.column, row.values]),
    );
  } catch {
    return {};
  }
}

/**
 * Approve, edit, or reject one semantic mapping.
 *
 * The decision is persisted by the backend and is the same record runtime
 * resolves against, so this is never a display-only status change.
 */
export async function reviewSemantic(
  dataSourceId: string,
  proposalId: string,
  action: "approve" | "reject",
  conceptName?: string,
): Promise<{ ok: boolean; message: string }> {
  const response = await fetch(
    `${BASE}/data-sources/${dataSourceId}/semantics/${proposalId}/review`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        action,
        concept_name: conceptName ?? null,
        reason: action === "reject" ? "Rejected by reviewer." : null,
      }),
    },
  );
  if (response.ok) {
    return {
      ok: true,
      message: action === "approve" ? "Confirmed." : "Rejected.",
    };
  }
  const payload: unknown = await response.json().catch(() => null);
  const detail =
    payload !== null && typeof payload === "object" && "detail" in payload
      ? String((payload as { detail: unknown }).detail)
      : "The review could not be completed.";
  return { ok: false, message: detail };
}

export interface ScanSummary {
  schemaFingerprint: string;
  schemaChanged: boolean;
  tableCount: number;
  proposedEntities: number;
  proposedAttributes: number;
  proposedRelationships: number;
  markedStale: number;
}

export async function scanDataSource(
  dataSourceId: string,
): Promise<{ ok: boolean; message: string; summary: ScanSummary | null }> {
  const response = await fetch(`${BASE}/data-sources/${dataSourceId}/scan`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      payload !== null && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail: unknown }).detail)
        : "The scan could not be completed.";
    return { ok: false, message: detail, summary: null };
  }
  const raw = (payload ?? {}) as Record<string, unknown>;
  return {
    ok: true,
    message: "Scan complete.",
    summary: {
      schemaFingerprint: str(raw.schema_fingerprint),
      schemaChanged: raw.schema_changed === true,
      tableCount: num(raw.table_count),
      proposedEntities: num(raw.proposed_entities),
      proposedAttributes: num(raw.proposed_attributes),
      proposedRelationships: num(raw.proposed_relationships),
      markedStale: num(raw.marked_stale),
    },
  };
}


/** Connection reference names a reviewer may register against. Names only. */
export async function fetchConnectionRefs(): Promise<string[]> {
  const response = await fetch(`${BASE}/connection-refs`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) return [];
  const payload: unknown = await response.json();
  return Array.isArray(payload)
    ? payload.filter((entry): entry is string => typeof entry === "string")
    : [];
}

/**
 * Register a datasource.
 *
 * There is deliberately no credential parameter. `connectionRef` must be one
 * of the names the server returned; a DSN or password has no way in.
 */
export async function registerDataSource(input: {
  name: string;
  databaseType: string;
  connectionRef: string;
}): Promise<{ ok: boolean; message: string }> {
  const response = await fetch(`${BASE}/data-sources`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      name: input.name,
      database_type: input.databaseType,
      connection_ref: input.connectionRef,
    }),
  });
  if (response.ok) return { ok: true, message: "Data source registered." };
  const payload: unknown = await response.json().catch(() => null);
  const detail =
    payload !== null && typeof payload === "object" && "detail" in payload
      ? String((payload as { detail: unknown }).detail)
      : "The data source could not be registered.";
  return { ok: false, message: detail };
}

export async function reindexDataSource(
  dataSourceId: string,
): Promise<{ ok: boolean; message: string }> {
  const response = await fetch(`${BASE}/data-sources/${dataSourceId}/reindex`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      payload !== null && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail: unknown }).detail)
        : "The reindex could not be completed.";
    return { ok: false, message: detail };
  }
  const raw = (payload ?? {}) as Record<string, unknown>;
  return {
    ok: true,
    message:
      `Reindexed ${num(raw.documents_indexed)} documents with ` +
      `${str(raw.embedding_model)} (${num(raw.embedding_dimension)}d).`,
  };
}
