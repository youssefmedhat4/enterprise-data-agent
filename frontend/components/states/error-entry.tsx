"use client";

import { motion } from "motion/react";
import { CircleSlash, CloudOff, RotateCw, ShieldX } from "lucide-react";

import { presentError, type ErrorTone } from "@/lib/format/errors";
import type { ExchangeFailure } from "@/hooks/use-conversation";
import { cn } from "@/lib/utils";

/**
 * A failed analysis, rendered in place so the question it belongs to stays
 * visible. Backend messages arrive pre-sanitised — no stack traces, provider
 * text, or policy internals ever reach this component.
 *
 * Deliberately not a red box: tone is carried by a thin accent edge and the
 * icon, so a transient outage does not read as a catastrophe.
 */
interface ErrorEntryProps {
  failure: ExchangeFailure;
  onRetry: () => void;
  disabled: boolean;
}

const TONE: Record<
  ErrorTone,
  { bar: string; text: string; Icon: typeof CloudOff }
> = {
  unavailable: { bar: "bg-warning", text: "text-warning", Icon: CloudOff },
  denied: { bar: "bg-destructive", text: "text-destructive", Icon: ShieldX },
  rejected: {
    bar: "bg-border-strong",
    text: "text-muted-foreground",
    Icon: CircleSlash,
  },
  unexpected: {
    bar: "bg-destructive",
    text: "text-destructive",
    Icon: CircleSlash,
  },
};

export function ErrorEntry({ failure, onRetry, disabled }: ErrorEntryProps) {
  const presentation = presentError(failure.code);
  const { bar, text, Icon } = TONE[presentation.tone];

  return (
    <motion.div
      role="alert"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
      className="relative ps-4"
    >
      <span
        aria-hidden="true"
        className={cn(
          "absolute inset-y-0 start-0 w-[2px] rounded-full opacity-70",
          bar,
        )}
      />

      <p className={cn("label-xs mb-2 flex items-center gap-1.5", text)}>
        <Icon className="size-3.5" aria-hidden="true" />
        {presentation.title}
      </p>

      <p className="measure text-[15px] leading-relaxed text-foreground">
        {failure.message}
      </p>
      <p className="measure mt-1.5 text-[13px] leading-relaxed text-muted-foreground">
        {presentation.guidance}
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        {presentation.allowRetry ? (
          <button
            type="button"
            onClick={onRetry}
            disabled={disabled}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-[12px] font-medium text-foreground transition-all hover:border-border-strong hover:bg-surface-raised active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50"
          >
            <RotateCw className="size-3.5" aria-hidden="true" />
            Try again
          </button>
        ) : null}
        {failure.requestId !== null ? (
          <span className="font-mono text-[10.5px] text-muted-foreground">
            {failure.requestId}
          </span>
        ) : null}
      </div>
    </motion.div>
  );
}
