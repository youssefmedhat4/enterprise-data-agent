import type { Exchange } from "@/hooks/use-conversation";

/**
 * Per-thread transcript persistence.
 *
 * The backend owns conversation *state* through LangGraph checkpointing, but it
 * exposes no endpoint to read a thread's history back. Without a local copy,
 * reopening a thread showed an empty page even though the server could still
 * answer follow-ups — so the client keeps its own record of what was displayed.
 *
 * Deliberately `sessionStorage`, not `localStorage`: transcripts contain real
 * query results, and enterprise rows should not sit on disk indefinitely,
 * surviving sign-out or an authorization change. Session scope keeps history
 * across thread switches and page reloads in the same tab, then clears when the
 * tab closes.
 */

const KEY_PREFIX = "eda.transcript:v1:";
/** Per-thread cap. Result rows are the bulk of this. */
const MAX_BYTES = 512_000;
const MAX_EXCHANGES = 40;

function key(threadId: string): string {
  return `${KEY_PREFIX}${threadId}`;
}

/** Only settled exchanges are worth restoring; pending work never resumes. */
function persistable(exchanges: Exchange[]): Exchange[] {
  return exchanges.filter(
    (exchange) => exchange.state === "answered" || exchange.state === "failed",
  );
}

export function saveTranscript(
  threadId: string | null,
  exchanges: Exchange[],
): void {
  if (typeof window === "undefined" || threadId === null) return;

  const settled = persistable(exchanges);
  if (settled.length === 0) return;

  try {
    let window_ = settled.slice(-MAX_EXCHANGES);
    let payload = JSON.stringify(window_);

    // Drop the oldest entries until the thread fits its budget. A huge result
    // set should cost old history, never the newest answer.
    while (payload.length > MAX_BYTES && window_.length > 1) {
      window_ = window_.slice(1);
      payload = JSON.stringify(window_);
    }
    if (payload.length > MAX_BYTES) return;

    sessionStorage.setItem(key(threadId), payload);
  } catch {
    // Quota exceeded or storage disabled. History is a convenience, not a
    // requirement — the thread still continues correctly on the server.
  }
}

export function loadTranscript(threadId: string): Exchange[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = sessionStorage.getItem(key(threadId));
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is Exchange =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as Exchange).id === "string" &&
        typeof (item as Exchange).question === "string",
    );
  } catch {
    return [];
  }
}

export function clearTranscript(threadId: string): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.removeItem(key(threadId));
  } catch {
    // Nothing to do — the entry is unreachable either way.
  }
}
