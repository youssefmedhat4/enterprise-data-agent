"use client";

import { ArrowRight, Clock, Database, ShieldCheck } from "lucide-react";

import { formatDuration, formatTimestamp } from "@/lib/format/values";
import { describeSource, isGovernedMetric } from "@/lib/format/source";
import type { AnalyticsResponse } from "@/lib/types/analytics";

/**
 * The trust line beneath an answer.
 *
 * Everything shown comes from the PUBLIC provenance block. Identity, policy,
 * model routing, and SQL are never inferred here — that data does not reach the
 * client unless the backend explicitly authorises a debug view.
 */
interface ProvenanceStripProps {
  response: AnalyticsResponse;
  onOpenDetails: () => void;
}

function Divider() {
  return (
    <span aria-hidden="true" className="text-border-strong">
      /
    </span>
  );
}

export function ProvenanceStrip({
  response,
  onOpenDetails,
}: ProvenanceStripProps) {
  const governed = isGovernedMetric(response.provenance.source);
  const executedAt = formatTimestamp(response.execution.executed_at);
  const freshness =
    response.freshness.status === "known"
      ? formatTimestamp(response.freshness.as_of)
      : null;

  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-2 border-t border-hairline pt-3 text-[11px] text-muted-foreground">
      {governed ? (
        <span className="inline-flex items-center gap-1.5 rounded-md bg-success/10 px-1.5 py-0.5 font-medium text-success">
          <ShieldCheck className="size-3" aria-hidden="true" />
          Governed metric
        </span>
      ) : (
        <span className="inline-flex items-center gap-1.5">
          <Database className="size-3" aria-hidden="true" />
          <span className="font-mono">
            {describeSource(response.provenance.source)}
          </span>
        </span>
      )}

      <Divider />
      <span className="tnum">
        {response.execution.row_count}
        {response.execution.row_count === 1 ? " row" : " rows"}
      </span>

      <Divider />
      <span className="tnum inline-flex items-center gap-1">
        <Clock className="size-3" aria-hidden="true" />
        {formatDuration(response.execution.duration_ms)}
      </span>

      {freshness !== null ? (
        <>
          <Divider />
          <span>as of {freshness}</span>
        </>
      ) : executedAt !== null ? (
        <>
          <Divider />
          <span>{executedAt}</span>
        </>
      ) : null}

      <button
        type="button"
        onClick={onOpenDetails}
        className="group ms-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      >
        Sources &amp; details
        <ArrowRight
          className="size-3 transition-transform duration-150 group-hover:translate-x-0.5"
          aria-hidden="true"
        />
      </button>
    </div>
  );
}
