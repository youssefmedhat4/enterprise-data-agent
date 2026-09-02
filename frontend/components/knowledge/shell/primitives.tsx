"use client";

import { motion } from "motion/react";
import { Slot } from "radix-ui";
import type { LucideIcon } from "lucide-react";

import { DUR, EASE_ENTRANCE } from "@/lib/motion";
import { cn } from "@/lib/utils";

/**
 * The pieces the Knowledge workspace is assembled from.
 *
 * Kept together because they only exist to give ten different sections one
 * voice: the same card surface, the same status vocabulary, the same empty
 * state. A reviewer moving between schema proposals and evaluation runs should
 * not feel they have changed product.
 *
 * Depth comes from the existing surface ladder — background, surface,
 * surface-raised — rather than from borders alone, so the dark theme reads as
 * layers instead of a flat sheet with lines drawn on it.
 */

/* ------------------------------------------------------------------ card -- */

export function Panel({
  className,
  interactive = false,
  asChild = false,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  interactive?: boolean;
  /** Render the caller's element as the panel — a form, say. */
  asChild?: boolean;
}) {
  const Component = asChild ? Slot.Root : "div";
  return (
    <Component
      className={cn(
        "rounded-xl border border-hairline bg-surface",
        "transition-[background-color,border-color,box-shadow,transform] duration-200",
        interactive &&
          "hover:-translate-y-px hover:border-border hover:bg-surface-raised hover:shadow-float",
        className,
      )}
      {...props}
    >
      {children}
    </Component>
  );
}

/* --------------------------------------------------------------- heading -- */

export function SectionHeader({
  title,
  description,
  action,
  count,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
  count?: number;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <div className="flex items-center gap-2.5">
          <h2 className="text-[19px] font-semibold tracking-tight text-foreground">
            {title}
          </h2>
          {count !== undefined && count > 0 ? (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
              {count}
            </span>
          ) : null}
        </div>
        <p className="measure mt-1.5 text-[13.5px] leading-relaxed text-muted-foreground">
          {description}
        </p>
      </div>
      {action !== undefined ? <div className="shrink-0">{action}</div> : null}
    </header>
  );
}

/* ---------------------------------------------------------------- status -- */

export type StatusTone = "neutral" | "positive" | "attention" | "critical" | "accent";

/**
 * Status vocabulary, deliberately narrow.
 *
 * Colour carries one meaning each: green is settled, amber wants a person,
 * red failed, blue is the system's own accent. Anything else is neutral, which
 * is most things — a dashboard where every badge is coloured has no signal
 * left to spend.
 */
const TONE_CLASS: Record<StatusTone, string> = {
  neutral: "bg-muted text-muted-foreground ring-border",
  positive: "bg-success/12 text-success ring-success/25",
  attention: "bg-warning/12 text-warning ring-warning/25",
  critical: "bg-destructive/12 text-destructive ring-destructive/25",
  accent: "bg-primary/12 text-primary ring-primary/25",
};

export function toneForStatus(status: string): StatusTone {
  switch (status.toUpperCase()) {
    case "READY":
    case "CONFIRMED":
    case "APPROVED":
    case "CERTIFIED":
    case "HEALTHY":
    case "PASS":
      return "positive";
    case "PROPOSED":
    case "WARNING":
    case "AWAITING":
      return "attention";
    case "STALE":
    case "REJECTED":
    case "FAILING":
    case "FAIL":
    case "ERROR":
    case "REGRESSION":
      return "critical";
    default:
      return "neutral";
  }
}

export function StatusBadge({
  children,
  tone,
  dot = false,
  className,
}: {
  children: React.ReactNode;
  tone: StatusTone;
  dot?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5",
        "text-[11px] font-medium tracking-wide ring-1 ring-inset",
        "transition-colors duration-200",
        TONE_CLASS[tone],
        className,
      )}
    >
      {dot ? (
        <span
          aria-hidden="true"
          className="size-1.5 rounded-full bg-current opacity-80"
        />
      ) : null}
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ stat -- */

export function StatCard({
  icon: Icon,
  label,
  value,
  context,
  tone = "neutral",
  index = 0,
}: {
  icon: LucideIcon;
  label: string;
  value: number | string;
  context: string;
  tone?: StatusTone;
  index?: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: DUR.base,
        ease: EASE_ENTRANCE,
        // Entrance reads left to right, so the row resolves as a sequence
        // rather than four things appearing at once.
        delay: index * 0.045,
      }}
    >
      <Panel interactive className="h-full p-5">
        <div className="flex items-center gap-2">
          <Icon
            className={cn(
              "size-3.5",
              tone === "attention" ? "text-warning" : "text-muted-foreground",
            )}
            aria-hidden="true"
          />
          <span className="text-[12.5px] font-medium text-muted-foreground">
            {label}
          </span>
        </div>
        <p
          className={cn(
            "mt-3 text-[30px] font-semibold leading-none tracking-tight tabular-nums",
            tone === "attention" ? "text-warning" : "text-foreground",
          )}
        >
          {value}
        </p>
        <p className="mt-2.5 text-[12.5px] leading-relaxed text-muted-foreground">
          {context}
        </p>
      </Panel>
    </motion.div>
  );
}

/* ----------------------------------------------------------------- empty -- */

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-dashed border-border px-6 py-14 text-center">
      <div className="mx-auto grid size-10 place-items-center rounded-lg bg-muted">
        <Icon className="size-[18px] text-muted-foreground" aria-hidden="true" />
      </div>
      <p className="mt-4 text-[14px] font-medium text-foreground">{title}</p>
      <p className="measure mx-auto mt-1.5 text-[13px] leading-relaxed text-muted-foreground">
        {description}
      </p>
      {action !== undefined ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

/* -------------------------------------------------------------- skeleton -- */

/**
 * Loading shapes that match what replaces them.
 *
 * A skeleton whose geometry differs from the real component makes the page
 * jump when data arrives, which is worse than showing nothing.
 */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-muted/70", className)}
    />
  );
}

export function CardSkeleton({ rows = 2 }: { rows?: number }) {
  return (
    <Panel className="p-5">
      <div className="flex items-start justify-between gap-4">
        <Skeleton className="h-4 w-44" />
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
      <div className="mt-4 space-y-2.5">
        {Array.from({ length: rows }).map((_, index) => (
          <Skeleton
            key={index}
            className={cn("h-3", index === rows - 1 ? "w-1/2" : "w-full")}
          />
        ))}
      </div>
    </Panel>
  );
}

export function StatRowSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: 4 }).map((_, index) => (
        <Panel key={index} className="p-5">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="mt-3.5 h-7 w-12" />
          <Skeleton className="mt-3 h-3 w-32" />
        </Panel>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------- key/value -- */

export function DetailRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex gap-3 py-1">
      <dt className="w-32 shrink-0 text-[12.5px] text-muted-foreground">
        {label}
      </dt>
      <dd
        className={cn(
          "min-w-0 flex-1 text-[13px] text-foreground",
          mono && "font-mono text-[12px] leading-5",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

/* ------------------------------------------------------------ section fx -- */

/**
 * Content settles after a navigation change: a short fade with a little lift.
 *
 * Mounted fresh by the tab panel each time a section opens, so the entrance
 * plays on arrival and nowhere else.
 */
export function SectionTransition({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: DUR.base, ease: EASE_ENTRANCE }}
      className="space-y-6"
    >
      {children}
    </motion.div>
  );
}
