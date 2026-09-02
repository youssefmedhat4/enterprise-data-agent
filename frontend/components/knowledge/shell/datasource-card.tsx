"use client";

import { Database, RefreshCw, Sparkles } from "lucide-react";

import {
  Panel,
  StatusBadge,
  toneForStatus,
} from "@/components/knowledge/shell/primitives";
import { Button } from "@/components/ui/button";
import type { DataSourceSummary } from "@/lib/datasources/datasources";
import { cn } from "@/lib/utils";

/**
 * One registered database, and the two things a reviewer can do to it.
 *
 * The connection reference stays where it was — a secret *name*, never a value
 * — but reads as a technical footnote rather than a headline, because it is not
 * what anyone comes to this card for.
 */
export function DatasourceCard({
  source,
  active,
  busy,
  onScan,
  onReindex,
}: {
  source: DataSourceSummary;
  active: boolean;
  busy: boolean;
  onScan: () => void;
  onReindex: () => void;
}) {
  const counts = [
    { label: "Certified metrics", value: source.certifiedMetricCount },
    { label: "Confirmed entities", value: source.confirmedEntityCount },
    { label: "Awaiting review", value: source.proposedEntityCount },
    { label: "Recurring patterns", value: source.recurringClusterCount },
  ];

  return (
    <Panel
      interactive
      className={cn("p-5", active && "border-primary/30 bg-surface-raised")}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Database className="size-4 text-muted-foreground" aria-hidden="true" />
            <h3 className="truncate text-[15px] font-medium text-foreground">
              {source.name}
            </h3>
            {active ? (
              <StatusBadge tone="accent">Selected</StatusBadge>
            ) : null}
          </div>
          <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted-foreground">
            <span>{source.databaseType}</span>
            <span aria-hidden="true">·</span>
            <span>
              {source.lastScannedAt === null
                ? "never scanned"
                : `scanned ${new Date(source.lastScannedAt).toLocaleDateString()}`}
            </span>
            <span aria-hidden="true">·</span>
            <span className="text-muted-foreground/70">
              reference{" "}
              <code className="font-mono text-[11.5px]">
                {source.connectionRef}
              </code>
            </span>
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <StatusBadge tone={toneForStatus(source.status)} dot>
            {source.status}
          </StatusBadge>
          <Button size="sm" variant="outline" disabled={busy} onClick={onScan}>
            <RefreshCw className="size-3.5" aria-hidden="true" />
            {source.lastScannedAt === null ? "Scan" : "Rescan"}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={onReindex}>
            <Sparkles className="size-3.5" aria-hidden="true" />
            Reindex semantic search
          </Button>
        </div>
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-4 border-t border-hairline pt-4 sm:grid-cols-4">
        {counts.map((entry) => (
          <div key={entry.label}>
            <dt className="text-[12px] text-muted-foreground">{entry.label}</dt>
            <dd className="mt-1 text-[17px] font-medium tabular-nums text-foreground">
              {entry.value}
            </dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}
