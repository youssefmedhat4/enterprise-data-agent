/**
 * Local thread metadata.
 *
 * The backend owns conversation state through LangGraph checkpointing and
 * exposes no thread-listing endpoint, so the client keeps only enough metadata
 * to reopen a recent analysis: the id, a display title, and timestamps.
 *
 * Nothing analytical is persisted here — no rows, answers, provenance, or
 * identity. Reopening a thread continues it on the server, it does not replay
 * a client-side transcript.
 */

const STORAGE_KEY = "eda.threads:v1";
const MAX_THREADS = 40;

export interface ThreadSummary {
  threadId: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  turnCount: number;
}

function isThreadSummary(value: unknown): value is ThreadSummary {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.threadId === "string" &&
    typeof candidate.title === "string" &&
    typeof candidate.createdAt === "number" &&
    typeof candidate.updatedAt === "number" &&
    typeof candidate.turnCount === "number"
  );
}

export function loadThreads(): ThreadSummary[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(isThreadSummary)
      .sort((a, b) => b.updatedAt - a.updatedAt);
  } catch {
    // Private browsing, quota, or corrupt payload — start clean rather than crash.
    return [];
  }
}

export function saveThreads(threads: ThreadSummary[]): void {
  if (typeof window === "undefined") return;
  try {
    const trimmed = [...threads]
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, MAX_THREADS);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {
    // Storage unavailable. Thread history is a convenience, never a requirement.
  }
}

/** Build a compact, readable title from the first question in a thread. */
export function deriveTitle(question: string): string {
  const collapsed = question.replace(/\s+/g, " ").trim();
  if (collapsed === "") return "Untitled analysis";
  const cut = collapsed.length > 64 ? `${collapsed.slice(0, 63).trimEnd()}…` : collapsed;
  return cut;
}

export function upsertThread(
  threads: ThreadSummary[],
  entry: { threadId: string; title: string },
): ThreadSummary[] {
  const now = Date.now();
  const existing = threads.find((thread) => thread.threadId === entry.threadId);
  if (existing === undefined) {
    return [
      {
        threadId: entry.threadId,
        title: entry.title,
        createdAt: now,
        updatedAt: now,
        turnCount: 1,
      },
      ...threads,
    ];
  }
  return threads.map((thread) =>
    thread.threadId === entry.threadId
      ? { ...thread, updatedAt: now, turnCount: thread.turnCount + 1 }
      : thread,
  );
}

export function removeThread(
  threads: ThreadSummary[],
  threadId: string,
): ThreadSummary[] {
  return threads.filter((thread) => thread.threadId !== threadId);
}

/** Group threads into the buckets a sidebar shows. */
export function groupThreads(
  threads: ThreadSummary[],
): { label: string; threads: ThreadSummary[] }[] {
  const now = new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  ).getTime();
  const startOfYesterday = startOfToday - 86_400_000;
  const startOfWeek = startOfToday - 6 * 86_400_000;

  const buckets: { label: string; threads: ThreadSummary[] }[] = [
    { label: "Today", threads: [] },
    { label: "Yesterday", threads: [] },
    { label: "Previous 7 days", threads: [] },
    { label: "Older", threads: [] },
  ];

  for (const thread of threads) {
    if (thread.updatedAt >= startOfToday) buckets[0].threads.push(thread);
    else if (thread.updatedAt >= startOfYesterday) buckets[1].threads.push(thread);
    else if (thread.updatedAt >= startOfWeek) buckets[2].threads.push(thread);
    else buckets[3].threads.push(thread);
  }

  return buckets.filter((bucket) => bucket.threads.length > 0);
}
