// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_DATA_SOURCE_ID } from "@/lib/datasources/datasources";
import { useConversation } from "@/hooks/use-conversation";
import { postAnalyticsQuery } from "@/lib/api/analytics";
import type { AnalyticsResponse } from "@/lib/types/analytics";

import { fetchConversation } from "@/lib/conversations/api";

vi.mock("@/lib/api/analytics", () => ({
  postAnalyticsQuery: vi.fn(),
}));

vi.mock("@/lib/conversations/api", () => ({
  fetchConversation: vi.fn(),
}));

const postQuery = vi.mocked(postAnalyticsQuery);
const readConversation = vi.mocked(fetchConversation);

function response(profile: "gemini_pro" | "gemini"): AnalyticsResponse {
  return {
    schema_version: "1.1",
    request_id: "request-1",
    thread_id: "thread-1",
    data_source_id: DEFAULT_DATA_SOURCE_ID,
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
    clarification_choices: [],
    data_quality: [],
    trace: null,
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
  readConversation.mockReset();
  sessionStorage.clear();
});

/**
 * Reopening a conversation is the behaviour this whole feature exists for:
 * before it, the transcript lived in session storage and a closed tab took it
 * with it, leaving an empty page above a composer that still worked.
 */
describe("restoring a stored conversation", () => {
  it("renders the questions and answers the server recorded", async () => {
    readConversation.mockResolvedValue({
      id: "conversation-1",
      title: "Payroll",
      dataSourceId: DEFAULT_DATA_SOURCE_ID,
      threadId: "thread-1",
      createdAt: 1,
      updatedAt: 2,
      messageCount: 4,
      hasMore: false,
      messages: [
        {
          id: "m0",
          role: "user",
          sequence: 0,
          content: "Which department has the highest payroll?",
          response: null,
          createdAt: 1,
        },
        {
          id: "m1",
          role: "assistant",
          sequence: 1,
          content: "Grounded answer",
          response: response("gemini"),
          createdAt: 1,
        },
      ],
    });

    const { result } = renderHook(() =>
      useConversation({ onThreadEstablished: vi.fn() }),
    );
    await act(() => result.current.openConversation("conversation-1"));

    expect(result.current.exchanges).toHaveLength(1);
    expect(result.current.exchanges[0]?.question).toBe(
      "Which department has the highest payroll?",
    );
    expect(result.current.exchanges[0]?.state).toBe("answered");
    expect(result.current.exchanges[0]?.response?.answer).toBe("Grounded answer");
    // The thread comes back with the transcript, so the next question
    // continues the same analytical context rather than starting over.
    expect(result.current.threadId).toBe("thread-1");
    expect(result.current.conversationId).toBe("conversation-1");
    expect(result.current.restoreNotice).toBeNull();
  });

  it("says so when a conversation predates persisted history", async () => {
    readConversation.mockResolvedValue({
      id: "conversation-old",
      title: "Older analysis",
      dataSourceId: DEFAULT_DATA_SOURCE_ID,
      threadId: "thread-old",
      createdAt: 1,
      updatedAt: 2,
      messageCount: 0,
      hasMore: false,
      messages: [],
    });

    const { result } = renderHook(() =>
      useConversation({ onThreadEstablished: vi.fn() }),
    );
    await act(() => result.current.openConversation("conversation-old"));

    expect(result.current.exchanges).toHaveLength(0);
    expect(result.current.restoreNotice).toMatch(/before chat history/);
  });

  it("starting a new analysis drops the restored thread", async () => {
    readConversation.mockResolvedValue({
      id: "conversation-1",
      title: "Payroll",
      dataSourceId: DEFAULT_DATA_SOURCE_ID,
      threadId: "thread-1",
      createdAt: 1,
      updatedAt: 2,
      messageCount: 2,
      hasMore: false,
      messages: [],
    });

    const { result } = renderHook(() =>
      useConversation({ onThreadEstablished: vi.fn() }),
    );
    await act(() => result.current.openConversation("conversation-1"));
    act(() => result.current.startNewAnalysis());

    // A new chat must not silently continue the previous checkpoint.
    expect(result.current.threadId).toBeNull();
    expect(result.current.conversationId).toBeNull();
    postQuery.mockResolvedValue(response("gemini_pro"));
    await act(() =>
      result.current.ask("Show payroll", "gemini_pro", DEFAULT_DATA_SOURCE_ID),
    );
    expect(postQuery).toHaveBeenCalledWith(
      expect.objectContaining({ thread_id: null }),
      expect.any(Object),
    );
  });
});

describe("useConversation model identity", () => {
  it("includes the selected profile in the request and remembers it on the exchange", async () => {
    postQuery.mockResolvedValue(response("gemini"));
    const { result } = renderHook(() =>
      useConversation({ onThreadEstablished: vi.fn() }),
    );

    await act(() => result.current.ask("Show payroll", "gemini", DEFAULT_DATA_SOURCE_ID));

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

    await act(() => result.current.ask("Show payroll", "gemini_pro", DEFAULT_DATA_SOURCE_ID));
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
