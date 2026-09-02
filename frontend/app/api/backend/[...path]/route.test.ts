import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GET, POST, PUT } from "./route";

/**
 * What the same-origin proxy will and will not forward.
 *
 * The allowlist is the whole security value of this route, and it is also the
 * thing most likely to fall behind: a knowledge surface whose path was never
 * added here fails with a 404 from Next, which the UI reports as the backend
 * being unavailable — a misleading symptom that points at the wrong service.
 * These pin both halves.
 */

const SOURCE = "00000000-0000-0000-0000-000000000001";
const OTHER = "11111111-1111-1111-1111-111111111111";

function upstreamReturns(status = 200) {
  const fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify({}), {
        status,
        headers: { "content-type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function call(
  handler: typeof GET,
  path: string,
  method: "GET" | "POST" | "PUT" = "GET",
) {
  const segments = path.split("/");
  return handler(
    new NextRequest(`http://localhost/api/backend/${path}`, { method }),
    { params: Promise.resolve({ path: segments }) },
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("the proxy allowlist", () => {
  it.each([
    `knowledge/data-sources`,
    `knowledge/data-sources/${SOURCE}/semantics`,
    `knowledge/data-sources/${SOURCE}/candidates`,
    `knowledge/data-sources/${SOURCE}/quality`,
    `knowledge/data-sources/${SOURCE}/time-policy`,
    `knowledge/data-sources/${SOURCE}/temporal-dimensions`,
    `knowledge/data-sources/${SOURCE}/evaluation-cases`,
    `knowledge/data-sources/${SOURCE}/evaluation-runs`,
    `knowledge/connection-refs`,
    `health`,
  ])("forwards %s", async (path) => {
    const fetchMock = upstreamReturns();
    const response = await call(GET, path);

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it.each([
    `knowledge/data-sources/${SOURCE}/quality/run`,
    `knowledge/data-sources/${SOURCE}/quality/${OTHER}/toggle`,
    `knowledge/data-sources/${SOURCE}/time-preview`,
    `knowledge/data-sources/${SOURCE}/semantics/${OTHER}/review`,
    `knowledge/data-sources/${SOURCE}/candidates/${OTHER}/review`,
    `knowledge/data-sources/${SOURCE}/scan`,
  ])("forwards a POST to %s", async (path) => {
    upstreamReturns(201);
    expect((await call(POST, path, "POST")).status).toBe(201);
  });

  it.each([
    `knowledge/data-sources/${SOURCE}/time-policy`,
    `knowledge/data-sources/${SOURCE}/evaluation-cases/${OTHER}`,
  ])("forwards a PUT to %s", async (path) => {
    upstreamReturns();
    expect((await call(PUT, path, "PUT")).status).toBe(200);
  });

  it.each([
    "analytics/admin",
    "knowledge/data-sources/not-a-uuid/quality",
    `knowledge/data-sources/${SOURCE}/quality/../../secrets`,
    `knowledge/data-sources/${SOURCE}/anything-else`,
    "",
  ])("refuses %s without calling the backend", async (path) => {
    const fetchMock = upstreamReturns();
    const response = await call(GET, path);

    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
