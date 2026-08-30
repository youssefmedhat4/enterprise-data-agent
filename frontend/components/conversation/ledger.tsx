"use client";

import { motion } from "motion/react";
import { useEffect, useRef } from "react";

import { AnalysisEntry } from "@/components/conversation/analysis-entry";
import { PendingEntry } from "@/components/conversation/pending-entry";
import { ErrorEntry } from "@/components/states/error-entry";
import type { Exchange } from "@/hooks/use-conversation";
import { entryEnter } from "@/lib/motion";
import type { AnalyticsResponse } from "@/lib/types/analytics";
import { modelDisplayName } from "@/lib/models/profiles";

/**
 * The analysis ledger.
 *
 * Each exchange is a numbered document section, not a chat message pair. The
 * index in the margin and the hairline above each entry are what make a long
 * thread scannable once a dozen analyses have accumulated.
 *
 * Autoscroll only engages when the reader is already near the bottom, so a late
 * result never yanks someone away from an earlier one.
 */
interface LedgerProps {
  exchanges: Exchange[];
  onRetry: (exchangeId: string) => void;
  onOpenDetails: (response: AnalyticsResponse) => void;
  isBusy: boolean;
}

export function Ledger({
  exchanges,
  onRetry,
  onOpenDetails,
  isBusy,
}: LedgerProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const count = exchanges.length;
  const lastState = exchanges[count - 1]?.state;

  useEffect(() => {
    const element = endRef.current;
    if (element === null) return;
    const scroller = element.closest("[data-conversation-scroll]");
    if (!(scroller instanceof HTMLElement)) return;

    const distanceFromBottom =
      scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
    if (distanceFromBottom < 280) {
      element.scrollIntoView({ block: "end", behavior: "smooth" });
    }
  }, [count, lastState]);

  return (
    <ol className="space-y-12">
      {exchanges.map((exchange, index) => (
        <motion.li
          key={exchange.id}
          variants={entryEnter}
          initial="hidden"
          animate="visible"
          className="scroll-mt-20"
        >
          {/* Section rule + index. The first entry needs no rule above it. */}
          <div className="mb-5 flex items-center gap-3">
            <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
              {String(index + 1).padStart(2, "0")}
            </span>
            <span className="rule-fade flex-1" aria-hidden="true" />
          </div>

          {/* The question: a restrained heading, never a bubble. */}
          <h2
            dir="auto"
            className="measure wrap-anywhere mb-6 text-[15px] font-medium leading-relaxed text-muted-foreground"
          >
            {exchange.question}
          </h2>

          <p className="-mt-4 mb-6 text-[11px] text-muted-foreground">
            {exchange.response?.model_display_name ?? modelDisplayName(exchange.modelProfile)}
          </p>

          {exchange.state === "pending" ? <PendingEntry /> : null}

          {exchange.state === "answered" && exchange.response !== undefined ? (
            <AnalysisEntry
              response={exchange.response}
              onOpenDetails={onOpenDetails}
            />
          ) : null}

          {exchange.state === "failed" && exchange.error !== undefined ? (
            <ErrorEntry
              failure={exchange.error}
              onRetry={() => onRetry(exchange.id)}
              disabled={isBusy}
            />
          ) : null}

          {exchange.state === "cancelled" ? (
            <p
              role="status"
              className="text-[13px] text-muted-foreground"
            >
              Analysis stopped before it finished.
            </p>
          ) : null}
        </motion.li>
      ))}
      <div ref={endRef} aria-hidden="true" />
    </ol>
  );
}
