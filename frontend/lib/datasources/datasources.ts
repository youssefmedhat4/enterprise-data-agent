/**
 * Registered analytics databases the workspace can query.
 *
 * The browser holds an id and a display name and nothing else. Connection
 * details, credentials and secret references stay on the backend; the admin API
 * returns the *name* of the environment variable holding a DSN, never the DSN,
 * and none of that reaches this module.
 */

export const DEFAULT_DATA_SOURCE_ID = "00000000-0000-0000-0000-000000000001";

export interface DataSourceSummary {
  id: string;
  name: string;
  databaseType: string;
  /** Name of the secret, never its value. */
  connectionRef: string;
  status: string;
  schemaFingerprint: string | null;
  isDefault: boolean;
  lastScannedAt: string | null;
  confirmedEntityCount: number;
  proposedEntityCount: number;
  certifiedMetricCount: number;
  recurringClusterCount: number;
}

export const DEFAULT_DATA_SOURCE: DataSourceSummary = {
  id: DEFAULT_DATA_SOURCE_ID,
  name: "Company Analytics",
  databaseType: "postgres",
  connectionRef: "DATABASE_URL",
  status: "READY",
  schemaFingerprint: null,
  isDefault: true,
  lastScannedAt: null,
  confirmedEntityCount: 0,
  proposedEntityCount: 0,
  certifiedMetricCount: 0,
  recurringClusterCount: 0,
};

interface RawDataSource {
  id?: unknown;
  name?: unknown;
  database_type?: unknown;
  connection_ref?: unknown;
  status?: unknown;
  schema_fingerprint?: unknown;
  is_default?: unknown;
  last_scanned_at?: unknown;
  confirmed_entity_count?: unknown;
  proposed_entity_count?: unknown;
  certified_metric_count?: unknown;
  recurring_cluster_count?: unknown;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function parseDataSource(raw: RawDataSource): DataSourceSummary | null {
  const id = text(raw.id);
  if (!id) return null;
  return {
    id,
    name: text(raw.name, id),
    databaseType: text(raw.database_type, "unknown"),
    connectionRef: text(raw.connection_ref),
    status: text(raw.status, "REGISTERED"),
    schemaFingerprint: typeof raw.schema_fingerprint === "string" ? raw.schema_fingerprint : null,
    isDefault: raw.is_default === true,
    lastScannedAt: typeof raw.last_scanned_at === "string" ? raw.last_scanned_at : null,
    confirmedEntityCount: count(raw.confirmed_entity_count),
    proposedEntityCount: count(raw.proposed_entity_count),
    certifiedMetricCount: count(raw.certified_metric_count),
    recurringClusterCount: count(raw.recurring_cluster_count),
  };
}

export function parseDataSources(payload: unknown): DataSourceSummary[] {
  if (!Array.isArray(payload)) return [];
  return payload
    .map((entry) => parseDataSource(entry as RawDataSource))
    .filter((entry): entry is DataSourceSummary => entry !== null);
}

export function dataSourceName(
  sources: readonly DataSourceSummary[],
  id: string,
): string {
  return sources.find((source) => source.id === id)?.name ?? "Company Analytics";
}

/**
 * Whether an exchange recorded against `exchangeSource` may be retried while
 * `activeSource` is selected.
 *
 * Retry must reuse the datasource the exchange was answered from: re-running a
 * question against a different database would silently answer a different
 * question.
 */
export function canRetryHere(
  exchangeSource: string | null,
  activeSource: string,
): boolean {
  return exchangeSource === null || exchangeSource === activeSource;
}
