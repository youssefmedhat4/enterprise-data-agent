import { apiFetch } from "@/lib/api/client";
import type { AnalyticsResponse } from "@/lib/types/analytics";

/**
 * Server-side conversation history.
 *
 * The transcript used to live in `sessionStorage`, which meant closing the tab
 * destroyed it while the backend still held enough context to answer a
 * follow-up — a blank page above a working composer. The server now owns the
 * record; this module only reads it.
 *
 * Nothing is cached to disk here. What the browser holds is whatever the last
 * request returned.
 */

export interface ConversationSummary {
  id: string;
  title: string;
  dataSourceId: string;
  /** The analytical thread this conversation continues. */
  threadId: string;
  createdAt: number;
  updatedAt: number;
  messageCount: number;
}

export type MessageRole = "user" | "assistant";

export interface ConversationMessage {
  id: string;
  role: MessageRole;
  sequence: number;
  content: string;
  /** Assistant turns: the bounded response the answer was rendered from. */
  response: AnalyticsResponse | null;
  createdAt: number;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
  /** Older turns exist above the ones returned. */
  hasMore: boolean;
}

interface RawSummary {
  id?: unknown;
  title?: unknown;
  data_source_id?: unknown;
  thread_id?: unknown;
  created_at?: unknown;
  updated_at?: unknown;
  message_count?: unknown;
}

interface RawMessage {
  id?: unknown;
  role?: unknown;
  sequence?: unknown;
  content?: unknown;
  payload?: unknown;
  request_id?: unknown;
  created_at?: unknown;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function moment(value: unknown): number {
  const parsed = typeof value === "string" ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : Date.now();
}

function toSummary(raw: RawSummary): ConversationSummary | null {
  const id = text(raw.id);
  if (id === "") return null;
  return {
    id,
    title: text(raw.title, "Untitled analysis"),
    dataSourceId: text(raw.data_source_id),
    threadId: text(raw.thread_id),
    createdAt: moment(raw.created_at),
    updatedAt: moment(raw.updated_at),
    messageCount:
      typeof raw.message_count === "number" ? raw.message_count : 0,
  };
}

/**
 * Rebuild the response an assistant turn was drawn from.
 *
 * The stored payload is the same public shape the browser received, minus the
 * request id (kept alongside it) and minus anything the debug policy gated —
 * so a restored answer can never show SQL that a fresh one would withhold.
 */
function toResponse(raw: RawMessage): AnalyticsResponse | null {
  const payload = raw.payload;
  if (typeof payload !== "object" || payload === null) return null;
  const candidate = payload as Record<string, unknown>;
  if (typeof candidate.answer !== "string") return null;
  return {
    ...(candidate as unknown as AnalyticsResponse),
    request_id: text(raw.request_id, "restored"),
  };
}

function toMessage(raw: RawMessage): ConversationMessage | null {
  const id = text(raw.id);
  const role = text(raw.role);
  if (id === "" || (role !== "user" && role !== "assistant")) return null;
  return {
    id,
    role,
    sequence: typeof raw.sequence === "number" ? raw.sequence : 0,
    content: text(raw.content),
    response: role === "assistant" ? toResponse(raw) : null,
    createdAt: moment(raw.created_at),
  };
}

export async function fetchConversations(): Promise<ConversationSummary[]> {
  const payload = await apiFetch<RawSummary[]>("/conversations");
  if (!Array.isArray(payload)) return [];
  return payload
    .map(toSummary)
    .filter((entry): entry is ConversationSummary => entry !== null);
}

export async function fetchConversation(
  conversationId: string,
): Promise<ConversationDetail | null> {
  const payload = await apiFetch<RawSummary & { messages?: unknown; has_more?: unknown }>(
    `/conversations/${conversationId}`,
  );
  const summary = toSummary(payload);
  if (summary === null) return null;
  const messages = Array.isArray(payload.messages)
    ? (payload.messages as RawMessage[])
        .map(toMessage)
        .filter((entry): entry is ConversationMessage => entry !== null)
    : [];
  return { ...summary, messages, hasMore: payload.has_more === true };
}

export function archiveConversation(conversationId: string): Promise<void> {
  return apiFetch<void>(`/conversations/${conversationId}`, {
    method: "DELETE",
  });
}

export function renameConversation(
  conversationId: string,
  title: string,
): Promise<void> {
  return apiFetch<void>(`/conversations/${conversationId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

/** Group conversations into the buckets the sidebar shows. */
export function groupConversations(
  conversations: readonly ConversationSummary[],
): { label: string; conversations: ConversationSummary[] }[] {
  const now = new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  ).getTime();
  const startOfYesterday = startOfToday - 86_400_000;
  const startOfWeek = startOfToday - 6 * 86_400_000;

  const buckets: { label: string; conversations: ConversationSummary[] }[] = [
    { label: "Today", conversations: [] },
    { label: "Yesterday", conversations: [] },
    { label: "Previous 7 days", conversations: [] },
    { label: "Older", conversations: [] },
  ];

  for (const conversation of conversations) {
    if (conversation.updatedAt >= startOfToday) {
      buckets[0].conversations.push(conversation);
    } else if (conversation.updatedAt >= startOfYesterday) {
      buckets[1].conversations.push(conversation);
    } else if (conversation.updatedAt >= startOfWeek) {
      buckets[2].conversations.push(conversation);
    } else {
      buckets[3].conversations.push(conversation);
    }
  }

  return buckets.filter((bucket) => bucket.conversations.length > 0);
}
