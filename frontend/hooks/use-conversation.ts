"use client";

import { useCallback, useRef, useState } from "react";

import { postAnalyticsQuery } from "@/lib/api/analytics";
import { ApiError } from "@/lib/api/client";
import { loadTranscript, saveTranscript } from "@/lib/threads/transcript";
import type { AnalyticsResponse } from "@/lib/types/analytics";
import type { ModelProfile } from "@/lib/models/profiles";

export interface ExchangeFailure {
  code: string;
  message: string;
  requestId: string | null;
  retryable: boolean;
}

/**
 * One question and its outcome. Pairing them keeps retry unambiguous — a retry
 * re-sends exactly this question on the same thread.
 */
export interface Exchange {
  id: string;
  question: string;
  askedAt: number;
  modelProfile: ModelProfile;
  state: "pending" | "answered" | "failed" | "cancelled";
  response?: AnalyticsResponse;
  error?: ExchangeFailure;
}

function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `x-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function toFailure(cause: unknown): ExchangeFailure {
  if (cause instanceof ApiError) {
    return {
      code: cause.code,
      message: cause.message,
      requestId: cause.requestId,
      retryable: cause.retryable,
    };
  }
  return {
    code: "internal_unexpected_error",
    message: "Something went wrong while running the analysis.",
    requestId: null,
    retryable: true,
  };
}

export interface UseConversationResult {
  exchanges: Exchange[];
  threadId: string | null;
  isBusy: boolean;
  ask: (question: string, modelProfile: ModelProfile) => Promise<void>;
  retry: (exchangeId: string) => Promise<void>;
  cancel: () => void;
  startNewAnalysis: () => void;
  resumeThread: (threadId: string) => void;
}

export function useConversation(options: {
  onThreadEstablished: (threadId: string, question: string) => void;
}): UseConversationResult {
  const { onThreadEstablished } = options;

  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  // The in-flight request, so the user can stop it.
  const abortRef = useRef<AbortController | null>(null);
  // Read inside callbacks only — subscribing to it would rerender on every turn.
  const threadRef = useRef<string | null>(null);

  const isBusy = pendingId !== null;

  const run = useCallback(
    async (exchangeId: string, question: string, modelProfile: ModelProfile) => {
      const controller = new AbortController();
      abortRef.current = controller;
      setPendingId(exchangeId);

      try {
        const response = await postAnalyticsQuery(
          {
            question,
            thread_id: threadRef.current,
            include_debug: true,
            model_profile: modelProfile,
          },
          { signal: controller.signal },
        );

        // The backend mints the thread id on the first turn and echoes it after.
        const established = threadRef.current === null;
        threadRef.current = response.thread_id;
        setThreadId(response.thread_id);
        if (established) {
          onThreadEstablished(response.thread_id, question);
        }

        setExchanges((current) => {
          const next = current.map((exchange) =>
            exchange.id === exchangeId
              ? ({ ...exchange, state: "answered", response, error: undefined } as Exchange)
              : exchange,
          );
          // Persist against the id the server just confirmed, so the very first
          // turn lands under the right thread rather than under `null`.
          saveTranscript(response.thread_id, next);
          return next;
        });
      } catch (cause) {
        const failure = toFailure(cause);
        const cancelled = failure.code === "request_cancelled";
        setExchanges((current) => {
          const next = current.map((exchange) =>
            exchange.id === exchangeId
              ? ({
                  ...exchange,
                  state: cancelled ? "cancelled" : "failed",
                  error: cancelled ? undefined : failure,
                } as Exchange)
              : exchange,
          );
          saveTranscript(threadRef.current, next);
          return next;
        });
      } finally {
        abortRef.current = null;
        setPendingId((current) => (current === exchangeId ? null : current));
      }
    },
    [onThreadEstablished],
  );

  const ask = useCallback(
    async (question: string, modelProfile: ModelProfile) => {
      const trimmed = question.trim();
      if (trimmed === "") return;
      const id = newId();
      setExchanges((current) => [
        ...current,
        {
          id,
          question: trimmed,
          askedAt: Date.now(),
          modelProfile,
          state: "pending",
        },
      ]);
      await run(id, trimmed, modelProfile);
    },
    [run],
  );

  const retry = useCallback(
    async (exchangeId: string) => {
      const exchange = exchanges.find((candidate) => candidate.id === exchangeId);
      if (exchange === undefined) return;
      setExchanges((current) =>
        current.map((exchange) => {
          if (exchange.id !== exchangeId) return exchange;
          return { ...exchange, state: "pending", error: undefined };
        }),
      );
      await run(exchangeId, exchange.question, exchange.modelProfile);
    },
    [exchanges, run],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const startNewAnalysis = useCallback(() => {
    abortRef.current?.abort();
    threadRef.current = null;
    setThreadId(null);
    setExchanges([]);
    setPendingId(null);
  }, []);

  /**
   * Reopen an existing server-side thread.
   *
   * LangGraph holds the authoritative analytical context; this restores the
   * locally recorded transcript so the thread reads as it did when the user
   * left it, instead of coming back blank.
   */
  const resumeThread = useCallback((nextThreadId: string) => {
    abortRef.current?.abort();
    threadRef.current = nextThreadId;
    setThreadId(nextThreadId);
    setExchanges(loadTranscript(nextThreadId));
    setPendingId(null);
  }, []);

  return {
    exchanges,
    threadId,
    isBusy,
    ask,
    retry,
    cancel,
    startNewAnalysis,
    resumeThread,
  };
}
