import type {
  MeasureFormat,
  PartToWholeDisplay,
  ResultRow,
  Scalar,
} from "@/lib/types/analytics";

/**
 * Value formatting for analytical results.
 *
 * The backend serialises PostgreSQL `numeric` as a JSON *string* (Decimal is not
 * a JSON type) while `int8`/`int4` arrive as real numbers. Both must render as
 * right-aligned figures and both must be chartable, so every numeric decision
 * here consults the declared column type first and the value shape second.
 *
 * Formatting is presentation only. The exact value the backend returned is
 * always preserved and exposed via `raw` so nothing is silently rounded away.
 */

/** PostgreSQL type names that carry a number. */
const NUMERIC_PG_TYPES = new Set([
  "numeric",
  "decimal",
  "int2",
  "int4",
  "int8",
  "smallint",
  "integer",
  "bigint",
  "float4",
  "float8",
  "real",
  "double precision",
  "money",
]);

const TEMPORAL_PG_TYPES = new Set([
  "date",
  "timestamp",
  "timestamptz",
  "timestamp with time zone",
  "timestamp without time zone",
]);

export type CellKind = "number" | "temporal" | "boolean" | "text" | "empty";

/** Matches a complete numeric literal, including the Decimal-as-string form. */
const NUMERIC_LITERAL = /^-?\d+(\.\d+)?([eE][+-]?\d+)?$/;

function looksNumeric(value: Scalar): boolean {
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed !== "" && NUMERIC_LITERAL.test(trimmed);
  }
  return false;
}

/**
 * Decide how a column should be treated.
 *
 * `column_types` is authoritative when present, but governed-metric results
 * report every type as `unknown`, so fall back to inspecting actual values.
 */
export function classifyColumn(
  column: string,
  columnTypes: Record<string, string>,
  rows: ResultRow[],
): CellKind {
  const declared = columnTypes[column]?.toLowerCase();

  if (declared !== undefined && declared !== "unknown") {
    if (NUMERIC_PG_TYPES.has(declared)) return "number";
    if (TEMPORAL_PG_TYPES.has(declared)) return "temporal";
    if (declared === "bool" || declared === "boolean") return "boolean";
    return "text";
  }

  // Inference path: every non-null value must agree, and there must be one.
  let seen = 0;
  let numeric = 0;
  let boolean = 0;
  for (const row of rows) {
    const value = row[column];
    if (value === null || value === undefined || value === "") continue;
    seen += 1;
    if (looksNumeric(value)) numeric += 1;
    if (typeof value === "boolean") boolean += 1;
    if (seen >= 50) break;
  }
  if (seen === 0) return "empty";
  if (numeric === seen) return "number";
  if (boolean === seen) return "boolean";
  return "text";
}

/**
 * Coerce a cell to a finite number, or `null` when it is not numeric.
 * Charts must never receive a string — Recharts would treat it as categorical.
 */
export function toNumber(value: Scalar): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed === "" || !NUMERIC_LITERAL.test(trimmed)) return null;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

const compactFormatter = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});

/**
 * Format a number for display.
 *
 * Integers keep no decimals. Fractional values show up to two, except very
 * small magnitudes where two would collapse to `0.00` and destroy the reading —
 * those keep enough significant digits to stay truthful.
 */
export function formatNumber(value: number): string {
  if (Number.isInteger(value)) {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(
      value,
    );
  }
  const magnitude = Math.abs(value);
  if (magnitude > 0 && magnitude < 0.01) {
    return new Intl.NumberFormat(undefined, {
      maximumSignificantDigits: 3,
    }).format(value);
  }
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

/**
 * Format a measure according to how the column is *stored*.
 *
 * `percent` appends a sign only because the stored values already are
 * percentages. It must never be used to express a share derived from the data —
 * that is `shareOfTotal`, and conflating the two is what turned a 710,000
 * payroll amount into "710,000%".
 *
 * `currency` is rendered with a fixed two decimals but deliberately no symbol:
 * the result carries no currency code, and this platform holds multi-currency
 * data, so printing "$" would assert something the data never said.
 */
export function formatMeasure(value: number, format: MeasureFormat): string {
  if (format === "percent") return `${formatNumber(value)}%`;
  if (format === "currency") {
    return new Intl.NumberFormat(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  }
  return formatNumber(value);
}

/**
 * A slice's share of the plotted total, as a percentage.
 *
 * Display-only and computed from the values already on the chart. Returns null
 * when the total is zero, negative, or non-finite, so a degenerate result shows
 * its raw values instead of a misleading or infinite share.
 */
export function shareOfTotal(value: number, total: number): number | null {
  if (!Number.isFinite(total) || !Number.isFinite(value) || total <= 0) return null;
  return (value / total) * 100;
}

/** Render a share with one decimal, which reads cleanly at chart scale. */
export function formatShare(share: number): string {
  return `${new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(share)}%`;
}

/**
 * Label one part-to-whole slice according to the spec's display mode.
 * Falls back to the value alone whenever a share cannot be computed safely.
 */
export function formatSliceLabel(
  value: number,
  total: number,
  valueFormat: MeasureFormat,
  display: PartToWholeDisplay,
): string {
  const formattedValue = formatMeasure(value, valueFormat);
  if (display === "value") return formattedValue;

  const share = shareOfTotal(value, total);
  if (share === null) return formattedValue;

  const formattedShare = formatShare(share);
  return display === "percent"
    ? formattedShare
    : `${formattedValue} · ${formattedShare}`;
}

/** Short axis/tick label: 710,000 -> 710K. */
export function formatCompact(value: number): string {
  return Math.abs(value) >= 10_000
    ? compactFormatter.format(value)
    : formatNumber(value);
}

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

export function formatTemporal(value: Scalar): string | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  const trimmed = value.trim();
  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) return null;
  if (DATE_ONLY.test(trimmed)) {
    return parsed.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
  }
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export interface FormattedCell {
  /** What the user sees. */
  display: string;
  /** Exactly what the backend returned, for the title attribute and copy/CSV. */
  raw: string;
  kind: CellKind;
  isNull: boolean;
}

export function formatCell(value: Scalar, kind: CellKind): FormattedCell {
  if (value === null || value === undefined) {
    return { display: "—", raw: "", kind, isNull: true };
  }

  const raw = String(value);

  if (kind === "number") {
    const numeric = toNumber(value);
    if (numeric !== null) {
      return { display: formatNumber(numeric), raw, kind, isNull: false };
    }
  }

  if (kind === "temporal") {
    const formatted = formatTemporal(value);
    if (formatted !== null) {
      return { display: formatted, raw, kind, isNull: false };
    }
  }

  if (kind === "boolean" && typeof value === "boolean") {
    return { display: value ? "Yes" : "No", raw, kind, isNull: false };
  }

  return { display: raw, raw, kind, isNull: false };
}

/**
 * Numeric literals in prose, bounded so a match can't be part of a larger
 * token like a UUID (`4f2a-1234`), an ISO date (`2026-08-25`), or a version
 * (`v1.00`).
 *
 * The trailing `(?!\.\d)` rather than a blanket `(?!\.)` matters: a figure that
 * ends a sentence is followed by a full stop, and rejecting that would leave the
 * last number in every answer unformatted while its siblings were rewritten.
 * Only a dot followed by another digit means the match is part of a longer number.
 */
const GROUNDED_NUMERAL = /(?<![\w.\-/:])(-?\d+(?:\.\d+)?)(?![\w\-/:])(?!\.\d)/g;

/**
 * Tidy the numbers inside a backend answer for display.
 *
 * The answer model is handed the raw result JSON, where PostgreSQL `numeric`
 * appears as a string, so it faithfully writes `710000.00` — and sometimes
 * `142000.000000000000` — into otherwise clean prose.
 *
 * This rewrites a numeral **only when its value is present in the returned
 * rows**. That restriction is the whole safety argument: a literal that matches
 * grounded data is a data value, so reformatting it cannot change analytical
 * meaning, while an order ID, year, or version never matches and is left alone.
 * Values are never rounded — only trailing zeros and separators change.
 */
export function formatGroundedNumbers(
  answer: string,
  rows: ResultRow[],
): string {
  if (rows.length === 0 || answer === "") return answer;

  // Every numeric value the backend actually returned, keyed by its number.
  const grounded = new Set<number>();
  for (const row of rows) {
    for (const value of Object.values(row)) {
      const numeric = toNumber(value);
      if (numeric !== null) grounded.add(numeric);
    }
  }
  if (grounded.size === 0) return answer;

  return answer.replace(GROUNDED_NUMERAL, (literal) => {
    const numeric = toNumber(literal);
    if (numeric === null || !grounded.has(numeric)) return literal;
    const formatted = formatNumber(numeric);
    // Leave it alone when formatting is a no-op, so untouched text stays byte-identical.
    return formatted === literal ? literal : formatted;
  });
}

/** `department_name` -> `Department name`. Backend column names are snake_case. */
export function humanizeColumn(column: string): string {
  const spaced = column.replace(/_/g, " ").trim();
  if (spaced === "") return column;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function formatDuration(ms: number): string {
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatTimestamp(value: string | null): string | null {
  if (value === null) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
