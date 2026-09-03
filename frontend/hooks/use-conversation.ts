"use client";

import { useCallback, useRef, useState } from "react";

import { postAnalyticsQuery } from "@/lib/api/analytics";
import { ApiError } from "@/lib/api/client";
import {
  fetchConversation,
  type ConversationMessage,
} from "@/lib/conversations/api";
import type { AnalyticsResponse } from "@/lib/types/analytics";
import {
  DEFAULT_MODEL_PROFILE,
  isModelProfile,
  type ModelProfile,
} from "@/lib/models/profiles";

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
  /**
   * Which database answered. Retained per exchange so a retry re-runs against
   * the same one: re-running against a different database would silently
   * answer a different question.
   */
  dataSourceId: string;
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
  /** The persisted conversation this thread belongs to, once one exists. */
  conversationId: string | null;
  isBusy: boolean;
  /** A stored transcript is being fetched; the ledger shows placeholders. */
  isRestoring: boolean;
  /** Set when a conversation predates persisted history, or could not load. */
  restoreNotice: string | null;
  ask: (
    question: string,
    modelProfile: ModelProfile,
    dataSourceId: string,
  ) => Promise<void>;
  retry: (exchangeId: string) => Promise<void>;
  cancel: () => void;
  startNewAnalysis: () => void;
  /**
   * Reopen a stored conversation, reporting which database it belongs to.
   *
   * The caller needs that: a restored Legacy ERP transcript above a composer
   * still pointed at Company Analytics would send the next follow-up to the
   * wrong database while continuing the first one's analytical context.
   */
  openConversation: (conversationId: string) => Promise<string | null>;
  /**
   * Attach to the conversation the server created while answering.
   *
   * The first turn of a new chat mints the conversation server-side, so the
   * client learns its id only by looking it up afterwards. Adopting it means a
   * later reload knows which transcript to ask for.
   */
  adoptConversation: (conversationId: string) => void;
}

/**
 * Turn a stored transcript back into the exchanges the ledger renders.
 *
 * Messages arrive as an ordered sequence of user and assistant turns. They are
 * paired here rather than stored pre-paired, because a question whose answer
 * never landed is a real state the transcript has to be able to express.
 */
function toExchanges(
  messages: readonly ConversationMessage[],
  dataSourceId: string,
): Exchange[] {
  const restored: Exchange[] = [];
  for (const message of messages) {
    if (message.role === "user") {
      restored.push({
        id: message.id,
        question: message.content,
        askedAt: message.createdAt,
        modelProfile: DEFAULT_MODEL_PROFILE,
        dataSourceId,
        state: "failed",
        error: {
          code: "history_incomplete",
          message: "This question has no recorded answer.",
          requestId: null,
          retryable: true,
        },
      });
      continue;
    }
    const open = restored.at(-1);
    if (open === undefined || open.state !== "failed") continue;
    const response = message.response;
    restored[restored.length - 1] = {
      ...open,
      state: response === null ? "failed" : "answered",
      response: response ?? undefined,
      error: response === null ? open.error : undefined,
      modelProfile:
        response !== null && isModelProfile(response.model_profile)
          ? response.model_profile
          : open.modelProfile,
      dataSourceId: response?.data_source_id ?? open.dataSourceId,
    };
  }
  return restored;
}

export function useConversation(options: {
  onThreadEstablished: (threadId: string, question: string) => void;
}): UseConversationResult {
  const { onThreadEstablished } = options;

  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [isRestoring, setIsRestoring] = useState(false);
  const [restoreNotice, setRestoreNotice] = useState<string | null>(null);

  // The in-flight request, so the user can stop it.
  const abortRef = useRef<AbortController | null>(null);
  // Read inside callbacks only — subscribing to it would rerender on every turn.
  const threadRef = useRef<string | null>(null);

  const isBusy = pendingId !== null;

  const run = useCallback(
    async (
      exchangeId: string,
      question: string,
      modelProfile: ModelProfile,
      dataSourceId: string,
    ) => {
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
            data_source_id: dataSourceId,
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

        setExchanges((current) =>
          current.map((exchange) =>
            exchange.id === exchangeId
              ? ({ ...exchange, state: "answered", response, error: undefined } as Exchange)
              : exchange,
          ),
        );
      } catch (cause) {
        const failure = toFailure(cause);
        const cancelled = failure.code === "request_cancelled";
        setExchanges((current) =>
          current.map((exchange) =>
            exchange.id === exchangeId
              ? ({
                  ...exchange,
                  state: cancelled ? "cancelled" : "failed",
                  error: cancelled ? undefined : failure,
                } as Exchange)
              : exchange,
          ),
        );
      } finally {
        abortRef.current = null;
        setPendingId((current) => (current === exchangeId ? null : current));
      }
    },
    [onThreadEstablished],
  );

  const ask = useCallback(
    async (
      question: string,
      modelProfile: ModelProfile,
      dataSourceId: string,
    ) => {
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
          dataSourceId,
          state: "pending",
        },
      ]);
      await run(id, trimmed, modelProfile, dataSourceId);
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
      await run(
        exchangeId,
        exchange.question,
        exchange.modelProfile,
        exchange.dataSourceId,
      );
    },
    [exchanges, run],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  /**
   * Start over.
   *
   * Clearing the thread reference is the whole point: the next question mints
   * a new thread key server-side, so a new chat cannot silently continue the
   * previous conversation's analytical context.
   */
  const startNewAnalysis = useCallback(() => {
    abortRef.current?.abort();
    threadRef.current = null;
    setThreadId(null);
    setConversationId(null);
    setExchanges([]);
    setPendingId(null);
    setRestoreNotice(null);
    setIsRestoring(false);
  }, []);

  /**
   * Reopen a stored conversation.
   *
   * The transcript comes from the server, which is the only copy that survives
   * a closed tab or a restarted process. Its thread id comes back with it, so
   * the follow-up the user types next continues the same analytical context
   * that produced the history they are looking at.
   */
  const openConversation = useCallback(async (nextConversationId: string) => {
    abortRef.current?.abort();
    setPendingId(null);
    setConversationId(nextConversationId);
    setIsRestoring(true);
    setRestoreNotice(null);
    setExchanges([]);
    try {
      const conversation = await fetchConversation(nextConversationId);
      if (conversation === null) {
        setRestoreNotice("This conversation could not be opened.");
        return null;
      }
      threadRef.current = conversation.threadId;
      setThreadId(conversation.threadId);
      setExchanges(toExchanges(conversation.messages, conversation.dataSourceId));
      if (conversation.messages.length === 0) {
        // Threads started before transcripts were persisted have analytical
        // context but nothing to show. Saying so is better than inventing
        // messages from what the learning store happens to remember.
        setRestoreNotice(
          "Analysis context is available, but this conversation was created " +
            "before chat history was persisted.",
        );
      }
      return conversation.dataSourceId;
    } catch {
      setRestoreNotice("This conversation's history could not be loaded.");
      return null;
    } finally {
      setIsRestoring(false);
    }
  }, []);

  const adoptConversation = useCallback((nextConversationId: string) => {
    setConversationId(nextConversationId);
  }, []);

  return {
    exchanges,
    threadId,
    conversationId,
    isBusy,
    isRestoring,
    restoreNotice,
    ask,
    retry,
    cancel,
    startNewAnalysis,
    openConversation,
    adoptConversation,
  };
}
