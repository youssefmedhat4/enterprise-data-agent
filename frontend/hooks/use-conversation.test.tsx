// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useConversation } from "@/hooks/use-conversation";
import { postAnalyticsQuery } from "@/lib/api/analytics";
import type { AnalyticsResponse } from "@/lib/types/analytics";

vi.mock("@/lib/api/analytics", () => ({
  postAnalyticsQuery: vi.fn(),
}));

const postQuery = vi.mocked(postAnalyticsQuery);

function response(profile: "gemini_pro" | "gemini"): AnalyticsResponse {
  return {
    schema_version: "1.1",
    request_id: "request-1",
    thread_id: "thread-1",
    model_profile: profile,
    model_display_name: profile === "gemini_pro" ? "Gemini 3.1 Pro Preview" : "Gemini 2.5 Flash",
    status: "completed",
    answer: "Grounded answer",
    columns: [],
    rows: [],
    chart: null,
    sources: [],
    provenance: {
      source: "test",
      tables: [],
      columns: [],
      result: {
        row_count: 0,
        columns: [],
        column_types: {},
        result_bytes: 0,
        truncated: false,
        live: false,
      },
      executed_at: null,
      freshness: { status: "unknown", as_of: null },
      debug: null,
    },
    freshness: { status: "unknown", as_of: null },
    clarification_required: false,
    clarification_question: null,
    warnings: [],
    execution: {
      query_id: null,
      status: "completed",
      row_count: 0,
      duration_ms: 1,
      executed_at: null,
      result_bytes: 0,
      truncated: false,
      live: false,
    },
  };
}

beforeEach(() => {
  postQuery.mockReset();
  sessionStorage.clear();
});

describe("useConversation model identity", () => {
  it("includes the selected profile in the request and remembers it on the exchange", async () => {
    postQuery.mockResolvedValue(response("gemini"));
    const { result } = renderHook(() =>
      useConversation({ onThreadEstablished: vi.fn() }),
    );

    await act(() => result.current.ask("Show payroll", "gemini"));

    expect(postQuery).toHaveBeenCalledWith(
      expect.objectContaining({ model_profile: "gemini" }),
      expect.any(Object),
    );
    expect(result.current.exchanges[0]?.modelProfile).toBe("gemini");
    expect(result.current.exchanges[0]?.response?.model_profile).toBe("gemini");
  });

  it("retries a failed exchange with its original profile", async () => {
    postQuery
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce(response("gemini_pro"));
    const { result } = renderHook(() =>
      useConversation({ onThreadEstablished: vi.fn() }),
    );

    await act(() => result.current.ask("Show payroll", "gemini_pro"));
    const exchangeId = result.current.exchanges[0]?.id;
    expect(exchangeId).toBeTruthy();

    await act(() => result.current.retry(exchangeId!));
    await waitFor(() => expect(result.current.exchanges[0]?.state).toBe("answered"));

    expect(postQuery).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ model_profile: "gemini_pro" }),
      expect.any(Object),
    );
  });
});
