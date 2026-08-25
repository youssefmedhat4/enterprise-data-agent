"use client";

import { motion } from "motion/react";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { SystemHealth } from "@/hooks/use-health";
import { cn } from "@/lib/utils";

/**
 * Ambient backend status.
 *
 * A single pulse of light, not a dashboard. `/health/ready` reports the
 * analytics database, the conversation checkpoint store, and — when the
 * deployment requires it — the governed metric provider.
 */

const LABELS = {
  checking: "Connecting",
  ready: "Systems ready",
  degraded: "Degraded",
  offline: "Unreachable",
} as const;

const DOT = {
  checking: "bg-muted-foreground",
  ready: "bg-success",
  degraded: "bg-warning",
  offline: "bg-destructive",
} as const;

const CHECK_LABELS: Record<string, string> = {
  database: "Analytics database",
  checkpoint: "Conversation memory",
  metric_provider: "Governed metrics",
};

export function SystemStatus({
  health,
  compact = false,
}: {
  health: SystemHealth;
  compact?: boolean;
}) {
  const entries = Object.entries(health.checks);

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={health.refresh}
          aria-label={`${LABELS[health.status]}. Re-check service status`}
          className={cn(
            "group flex items-center rounded-md text-[12px] text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground",
            compact ? "size-9 justify-center" : "min-w-0 flex-1 gap-2 px-2 py-1.5",
          )}
        >
          <span className="relative grid size-2 shrink-0 place-items-center">
            <span
              aria-hidden="true"
              className={cn("size-1.5 rounded-full", DOT[health.status])}
            />
            {health.status === "ready" || health.status === "checking" ? (
              <motion.span
                aria-hidden="true"
                className={cn(
                  "absolute inset-0 rounded-full",
                  DOT[health.status],
                )}
                animate={{ opacity: [0.5, 0, 0.5], scale: [1, 2.2, 1] }}
                transition={{
                  duration: 2.6,
                  repeat: Infinity,
                  ease: "easeInOut",
                }}
              />
            ) : null}
          </span>
          {compact ? null : (
            <span className="min-w-0 flex-1 truncate text-start">
              {LABELS[health.status]}
            </span>
          )}
        </button>
      </TooltipTrigger>
      <TooltipContent side="right" align="end" className="max-w-64">
        <p className="font-medium">{LABELS[health.status]}</p>
        {health.message !== null ? (
          <p className="mt-1 text-muted-foreground">{health.message}</p>
        ) : null}
        {entries.length > 0 ? (
          <ul className="mt-1.5 space-y-0.5">
            {entries.map(([key, value]) => (
              <li key={key} className="flex justify-between gap-4">
                <span className="text-muted-foreground">
                  {CHECK_LABELS[key] ?? key}
                </span>
                <span>{value === "ok" ? "Ready" : "Not required"}</span>
              </li>
            ))}
          </ul>
        ) : null}
        <p className="mt-1.5 text-muted-foreground">Click to re-check.</p>
      </TooltipContent>
    </Tooltip>
  );
}
