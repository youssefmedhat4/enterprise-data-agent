"use client";

import {
  ArrowRight,
  CheckCircle2,
  Gauge,
  Inbox,
  Layers,
  Repeat,
} from "lucide-react";

import {
  DetailRow,
  Panel,
  SectionHeader,
  StatCard,
  StatRowSkeleton,
  StatusBadge,
  toneForStatus,
} from "@/components/knowledge/shell/primitives";
import type { DataSourceSummary } from "@/lib/datasources/datasources";

/**
 * What is true about this database right now, and what is waiting on a person.
 *
 * Everything here is counted from data the console already loaded — no extra
 * request, and nothing estimated. The queue comes first because it is the only
 * part that asks anything of the reader; the totals below it are context for
 * judging whether those numbers are surprising.
 */

export interface OverviewCounts {
  proposals: number;
  pendingCandidates: number;
  stale: number;
  confirmed: number;
  metrics: number;
  examples: number;
  clusters: number;
}

interface Task {
  key: string;
  message: string;
  section: string;
  cta: string;
}

export function KnowledgeOverview({
  source,
  counts,
  loading,
  onNavigate,
}: {
  source: DataSourceSummary;
  counts: OverviewCounts;
  loading: boolean;
  onNavigate: (section: string) => void;
}) {
  const waiting = counts.proposals + counts.pendingCandidates;

  const tasks: Task[] = [];
  if (counts.proposals > 0) {
    tasks.push({
      key: "proposals",
      message: `${counts.proposals} schema ${counts.proposals === 1 ? "proposal is" : "proposals are"} awaiting review.`,
      section: "review",
      cta: "Review schema",
    });
  }
  if (counts.pendingCandidates > 0) {
    tasks.push({
      key: "candidates",
      message: `${counts.pendingCandidates} learned ${counts.pendingCandidates === 1 ? "candidate needs" : "candidates need"} a decision.`,
      section: "candidates",
      cta: "Open candidates",
    });
  }
  if (counts.stale > 0) {
    tasks.push({
      key: "stale",
      message: `${counts.stale} confirmed ${counts.stale === 1 ? "mapping" : "mappings"} went stale when the schema changed.`,
      section: "confirmed",
      cta: "See what changed",
    });
  }
  if (source.lastScannedAt === null) {
    tasks.push({
      key: "scan",
      message:
        "This database has never been scanned, so nothing is known about its tables yet.",
      section: "sources",
      cta: "Go to data sources",
    });
  }

  return (
    <>
      <SectionHeader
        title="Overview"
        description={`Everything the system has learned about ${source.name}, and what is waiting on a reviewer.`}
      />

      {loading ? (
        <StatRowSkeleton />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            index={0}
            icon={Inbox}
            label="Awaiting review"
            value={waiting}
            tone={waiting > 0 ? "attention" : "neutral"}
            context={
              waiting > 0
                ? "Schema proposals and candidates needing a decision."
                : "Nothing is queued for a reviewer."
            }
          />
          <StatCard
            index={1}
            icon={Layers}
            label="Confirmed semantics"
            value={counts.confirmed}
            context="Approved mappings that shape how questions are read."
          />
          <StatCard
            index={2}
            icon={Gauge}
            label="Certified metrics"
            value={counts.metrics}
            context="Definitions the system is allowed to answer from."
          />
          <StatCard
            index={3}
            icon={Repeat}
            label="Recurring questions"
            value={counts.clusters}
            context="Analytical shapes asked more than once."
          />
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel className="p-5">
          <h2 className="text-[14px] font-medium text-foreground">
            Waiting on you
          </h2>

          {tasks.length === 0 ? (
            <p className="mt-3 flex items-center gap-2 text-[13px] text-muted-foreground">
              <CheckCircle2 className="size-4 text-success" aria-hidden="true" />
              Nothing needs a decision right now.
            </p>
          ) : (
            <ul className="mt-3 divide-y divide-hairline">
              {tasks.map((task) => (
                <li
                  key={task.key}
                  className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
                >
                  <p className="min-w-0 text-[13px] text-foreground">
                    {task.message}
                  </p>
                  <button
                    type="button"
                    onClick={() => onNavigate(task.section)}
                    className="inline-flex shrink-0 items-center gap-1 rounded-md text-[13px] font-medium text-primary outline-none transition-colors hover:text-primary/80 focus-visible:ring-[3px] focus-visible:ring-ring/50"
                  >
                    {task.cta}
                    <ArrowRight className="size-3.5" aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel className="p-5">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-[14px] font-medium text-foreground">
              This data source
            </h2>
            <StatusBadge tone={toneForStatus(source.status)} dot>
              {source.status}
            </StatusBadge>
          </div>
          <dl className="mt-3">
            <DetailRow label="Name" value={source.name} />
            <DetailRow label="Engine" value={source.databaseType} />
            <DetailRow
              label="Last scanned"
              value={
                source.lastScannedAt === null
                  ? "never"
                  : new Date(source.lastScannedAt).toLocaleString()
              }
            />
            <DetailRow
              label="Approved examples"
              value={String(counts.examples)}
            />
          </dl>
        </Panel>
      </div>
    </>
  );
}
