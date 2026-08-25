"use client";

import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";

import { DUR, EASE_OUT } from "@/lib/motion";

/**
 * Pending analysis.
 *
 * The backend does not stream, so nothing here imitates token-by-token output.
 * Instead the stage label advances through phases the public contract actually
 * guarantees occur, and a skeleton holds the shape the result will take so the
 * layout does not jump when it lands.
 */
const STAGES = [
  { at: 0, label: "Interpreting the question" },
  { at: 3_500, label: "Resolving business context" },
  { at: 10_000, label: "Executing the analysis" },
  { at: 26_000, label: "Still working — complex questions take longer" },
] as const;

export function PendingEntry() {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Date.now() - started), 500);
    return () => clearInterval(timer);
  }, []);

  const stage =
    [...STAGES].reverse().find((candidate) => elapsed >= candidate.at) ??
    STAGES[0];

  return (
    <div className="min-w-0 space-y-6" aria-busy="true">
      <div className="flex items-center gap-2.5">
        {/* Three-phase pulse: quiet, deliberate, never a spinner. */}
        <span className="flex items-center gap-1" aria-hidden="true">
          {[0, 1, 2].map((index) => (
            <motion.span
              key={index}
              className="size-1.5 rounded-full bg-primary"
              animate={{ opacity: [0.25, 1, 0.25] }}
              transition={{
                duration: 1.4,
                repeat: Infinity,
                ease: "easeInOut",
                delay: index * 0.18,
              }}
            />
          ))}
        </span>

        <AnimatePresence mode="wait">
          <motion.span
            key={stage.label}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: DUR.fast, ease: EASE_OUT }}
            role="status"
            aria-live="polite"
            className="text-[14px] text-muted-foreground"
          >
            {stage.label}
          </motion.span>
        </AnimatePresence>

        {elapsed >= 6_000 ? (
          <span className="tnum text-[11px] text-muted-foreground">
            {Math.round(elapsed / 1000)}s
          </span>
        ) : null}
      </div>

      {/* Skeleton matches the answer-then-evidence shape of a real result. */}
      <div className="space-y-2.5" aria-hidden="true">
        <div className="h-3.5 w-[88%] rounded bg-muted" />
        <div className="h-3.5 w-[64%] rounded bg-muted" />
      </div>

      <div
        aria-hidden="true"
        className="relative overflow-hidden rounded-xl border border-border bg-surface"
      >
        <div className="animate-sheen absolute inset-0 overflow-hidden" />
        <div className="h-11 border-b border-border bg-surface-raised/40" />
        <div className="flex h-[240px] items-end gap-3 px-6 pb-10 pt-6">
          {[54, 80, 42, 66, 32].map((height, index) => (
            <div
              key={index}
              className="flex-1 rounded-t bg-muted"
              style={{ height: `${height}%` }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
