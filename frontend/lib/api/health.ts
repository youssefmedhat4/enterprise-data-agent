import { apiFetch } from "@/lib/api/client";
import type { HealthResponse } from "@/lib/types/analytics";

/** `GET /health/live` — process liveness only. */
export function getLiveness(signal?: AbortSignal): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health/live", {
    signal,
    timeoutMs: 8_000,
  });
}

/**
 * `GET /health/ready` — database and checkpoint readiness, plus the metric
 * provider when `READINESS_REQUIRE_METRIC_PROVIDER=1`. Returns 503 with the
 * standard error envelope when a dependency is down.
 */
export function getReadiness(signal?: AbortSignal): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health/ready", {
    signal,
    timeoutMs: 15_000,
  });
}
