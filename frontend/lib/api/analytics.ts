import { apiFetch, type RequestOptions } from "@/lib/api/client";
import type { AnalyticsRequest, AnalyticsResponse } from "@/lib/types/analytics";

/**
 * `POST /analytics/query`.
 *
 * Pass the `thread_id` returned by a previous response to continue the same
 * analytical thread. Omitting it starts a new one; the backend mints the id and
 * always returns it.
 */
export function postAnalyticsQuery(
  request: AnalyticsRequest,
  options: RequestOptions = {},
): Promise<AnalyticsResponse> {
  return apiFetch<AnalyticsResponse>("/analytics/query", {
    method: "POST",
    body: JSON.stringify(request),
    ...options,
  });
}
