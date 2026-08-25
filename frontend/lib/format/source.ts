/**
 * Presentation helpers for the public `provenance.source` string.
 *
 * Observed real values: `metric:cube`, `postgres:enterprise_analytics`,
 * `synthetic-enterprise`. The prefix before `:` names the execution surface.
 * Nothing here infers provider internals — it only makes the label readable.
 */

/** Governed metric results are sourced as `metric:<provider>`. */
export function isGovernedMetric(source: string): boolean {
  return source.startsWith("metric:");
}

export function describeSource(source: string): string {
  if (isGovernedMetric(source)) {
    return "Governed metric";
  }
  const [scheme, rest] = source.split(":", 2);
  if (rest !== undefined && rest !== "") {
    if (scheme === "postgres") return rest;
    return rest;
  }
  return source;
}

/** `analytics.employees` -> `employees` for compact display, schema in title. */
export function shortTableName(identifier: string): string {
  const index = identifier.lastIndexOf(".");
  return index === -1 ? identifier : identifier.slice(index + 1);
}
