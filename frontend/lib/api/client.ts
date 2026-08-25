import type { ErrorCode, ErrorResponse } from "@/lib/types/analytics";

/**
 * Browser-side HTTP client.
 *
 * Requests go to a same-origin Next.js route (`/api/backend/...`) which proxies
 * to FastAPI. That keeps the backend origin server-side and means the backend
 * needs no CORS configuration at all.
 */
export const BACKEND_PROXY_PREFIX = "/api/backend";

/**
 * A normalised failure. Every error surfaced to the UI is one of these, so
 * components never see a raw fetch rejection or a provider stack trace.
 */
export class ApiError extends Error {
  readonly code: ErrorCode | "network_unreachable" | "request_cancelled";
  readonly requestId: string | null;
  readonly retryable: boolean;
  readonly status: number | null;

  constructor(init: {
    code: ErrorCode | "network_unreachable" | "request_cancelled";
    message: string;
    requestId?: string | null;
    retryable?: boolean;
    status?: number | null;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.code = init.code;
    this.requestId = init.requestId ?? null;
    this.retryable = init.retryable ?? false;
    this.status = init.status ?? null;
  }
}

/**
 * Supplies the bearer token for outgoing requests.
 *
 * Local development authentication needs no token — the backend's local adapter
 * returns a fixed identity and ignores credentials entirely. A future OIDC /
 * Entra provider plugs in here by returning an access token; nothing else in the
 * analytics application changes.
 */
export type TokenProvider = () => Promise<string | null> | string | null;

let tokenProvider: TokenProvider = () => null;

export function setTokenProvider(provider: TokenProvider): void {
  tokenProvider = provider;
}

function isErrorResponse(value: unknown): value is ErrorResponse {
  if (typeof value !== "object" || value === null) return false;
  const candidate = (value as { error?: unknown }).error;
  if (typeof candidate !== "object" || candidate === null) return false;
  return (
    typeof (candidate as { code?: unknown }).code === "string" &&
    typeof (candidate as { message?: unknown }).message === "string"
  );
}

export interface RequestOptions {
  signal?: AbortSignal;
  /** Milliseconds before the request is aborted. Model calls are slow. */
  timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 180_000;

export async function apiFetch<T>(
  path: string,
  init: RequestInit & RequestOptions = {},
): Promise<T> {
  const { signal, timeoutMs = DEFAULT_TIMEOUT_MS, ...rest } = init;

  const timeoutController = new AbortController();
  const timer = setTimeout(() => timeoutController.abort(), timeoutMs);

  // Combine caller cancellation with the timeout.
  const signals = [timeoutController.signal, signal].filter(
    (candidate): candidate is AbortSignal => candidate !== undefined,
  );
  const combined =
    signals.length > 1 ? AbortSignal.any(signals) : signals[0];

  const token = await tokenProvider();
  const headers = new Headers(rest.headers);
  headers.set("Accept", "application/json");
  if (rest.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (token !== null && token !== "") {
    headers.set("Authorization", `Bearer ${token}`);
  }

  let response: Response;
  try {
    response = await fetch(`${BACKEND_PROXY_PREFIX}${path}`, {
      ...rest,
      headers,
      signal: combined,
    });
  } catch {
    clearTimeout(timer);
    if (signal?.aborted) {
      throw new ApiError({
        code: "request_cancelled",
        message: "The request was cancelled.",
      });
    }
    if (timeoutController.signal.aborted) {
      throw new ApiError({
        code: "query_timeout",
        message:
          "The request took too long to complete and was stopped. The analysis may still be running on the server.",
        retryable: true,
      });
    }
    throw new ApiError({
      code: "network_unreachable",
      message:
        "Could not reach the analytics service. Check that the backend is running.",
      retryable: true,
      status: null,
    });
  } finally {
    clearTimeout(timer);
  }

  const requestId = response.headers.get("X-Request-ID");

  if (response.status === 204) {
    return undefined as T;
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    if (isErrorResponse(payload)) {
      throw new ApiError({
        code: payload.error.code,
        message: payload.error.message,
        requestId: payload.error.request_id || requestId,
        retryable: payload.error.retryable,
        status: response.status,
      });
    }
    throw new ApiError({
      code: "internal_unexpected_error",
      message: "The analytics service returned an unexpected response.",
      requestId,
      retryable: response.status >= 500,
      status: response.status,
    });
  }

  return payload as T;
}
