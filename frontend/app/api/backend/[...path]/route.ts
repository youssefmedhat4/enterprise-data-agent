import { NextResponse, type NextRequest } from "next/server";

/**
 * Same-origin proxy to the FastAPI analytics service.
 *
 * The browser only ever talks to this Next.js route, so the backend needs no
 * CORS configuration and its origin is never exposed to the client bundle.
 * A future OIDC integration attaches the access token here, server-side.
 */

const BACKEND_ORIGIN = (
  process.env.ANALYTICS_API_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

/** Only these paths are proxied. The route is not an open relay. */
const ALLOWED_PATHS = ["analytics/query", "health", "health/live", "health/ready"];

/**
 * Knowledge administration paths, matched by shape rather than listed one by
 * one because they carry a datasource id and a resource id. Kept as an explicit
 * pattern so the route still refuses anything it does not recognise: the
 * backend enforces review authority regardless, but the proxy should not be a
 * general tunnel to it.
 */
const KNOWLEDGE_PATH =
  /^knowledge\/data-sources(\/[0-9a-f-]{36}\/(semantics|clusters|candidates|metrics|examples)(\/[0-9a-f-]{36}\/review)?)?$/;

function isAllowed(target: string): boolean {
  return ALLOWED_PATHS.includes(target) || KNOWLEDGE_PATH.test(target);
}

/** Long enough for a model-backed analytical query to finish. */
const UPSTREAM_TIMEOUT_MS = 240_000;

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function reject(status: number, code: string, message: string) {
  return NextResponse.json(
    { error: { code, message, request_id: "proxy", retryable: status >= 500 } },
    { status },
  );
}

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const target = path.join("/");

  if (!isAllowed(target)) {
    return reject(404, "invalid_request", "Unknown analytics endpoint.");
  }

  const headers = new Headers();
  headers.set("Accept", "application/json");

  const contentType = request.headers.get("content-type");
  if (contentType !== null) {
    headers.set("Content-Type", contentType);
  }

  // Forward the caller's bearer token unchanged. Local development sends none;
  // the backend's local adapter ignores credentials and returns a fixed identity.
  const authorization = request.headers.get("authorization");
  if (authorization !== null) {
    headers.set("Authorization", authorization);
  }

  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.text();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);

  try {
    const upstream = await fetch(`${BACKEND_ORIGIN}/${target}`, {
      method: request.method,
      headers,
      body,
      signal: controller.signal,
      cache: "no-store",
    });

    const payload = await upstream.text();
    const response = new NextResponse(payload, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("content-type") ?? "application/json",
        "Cache-Control": "no-store",
      },
    });

    // Surface the backend correlation id so the UI can quote it in errors.
    const requestId = upstream.headers.get("x-request-id");
    if (requestId !== null) {
      response.headers.set("X-Request-ID", requestId);
    }
    return response;
  } catch {
    if (controller.signal.aborted) {
      return reject(
        504,
        "query_timeout",
        "The analytics service did not respond in time.",
      );
    }
    return reject(
      503,
      "database_unavailable",
      "The analytics service is unreachable.",
    );
  } finally {
    clearTimeout(timer);
  }
}

export const GET = proxy;
export const POST = proxy;
