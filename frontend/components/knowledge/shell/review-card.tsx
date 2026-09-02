"use client";

import { Check, X } from "lucide-react";

import {
  DetailRow,
  Panel,
  StatusBadge,
  toneForStatus,
} from "@/components/knowledge/shell/primitives";
import { Button } from "@/components/ui/button";
import type { KnowledgeCandidate } from "@/lib/knowledge/knowledge";

/**
 * The candidate card, and the action row a review decision is made from.
 *
 * The rule it follows: whatever is being approved has to be legible *before*
 * the buttons are reachable. Approve is the affirmative action and looks like
 * it; reject is available without shouting, because rejecting a proposal is
 * ordinary here rather than a failure.
 */

function ReviewActions({
  busy,
  approveLabel,
  onApprove,
  onReject,
}: {
  busy: boolean;
  approveLabel: string;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div className="flex shrink-0 items-center gap-1.5">
      <Button size="sm" disabled={busy} onClick={onApprove}>
        <Check className="size-3.5" aria-hidden="true" />
        {approveLabel}
      </Button>
      <Button
        size="sm"
        variant="ghost"
        disabled={busy}
        onClick={onReject}
        className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
      >
        <X className="size-3.5" aria-hidden="true" />
        Reject
      </Button>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

/** A metric, rule or dimension the system is proposing be made official. */
export function CandidateCard({
  candidate,
  busy,
  onApprove,
  onReject,
}: {
  candidate: KnowledgeCandidate;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const pending = candidate.status === "PROPOSED";

  return (
    <Panel interactive className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="text-[15px] font-medium text-foreground">
            {candidate.displayName}
          </h3>
          <p className="measure mt-1 text-[13px] leading-relaxed text-muted-foreground">
            {candidate.description || "Suggested from a recurring pattern."}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <StatusBadge tone="neutral">{candidate.candidateType}</StatusBadge>
          <StatusBadge tone={toneForStatus(candidate.status)} dot>
            {candidate.status}
          </StatusBadge>
        </div>
      </div>

      <dl className="mt-4 border-t border-hairline pt-3">
        <DetailRow
          label="Observed"
          value={`${candidate.evidenceCount} times · ${candidate.successfulEvidenceCount} successful`}
        />
        {candidate.expression !== null ? (
          <DetailRow label="Calculation" value={candidate.expression} mono />
        ) : null}
        {candidate.grain !== null ? (
          <DetailRow label="Grain" value={candidate.grain} />
        ) : null}
        {candidate.dependencies.length > 0 ? (
          <DetailRow
            label="Depends on"
            value={candidate.dependencies.join(", ")}
          />
        ) : null}
        {/* What this kind of proposal actually says. A reviewer shown only a
            name is being asked to approve something they cannot see. */}
        {candidate.detail.map((item) => (
          <DetailRow key={item.label} label={item.label} value={item.value} />
        ))}
        {candidate.rejectionReason !== null ? (
          <DetailRow
            label="Rejected because"
            value={candidate.rejectionReason}
          />
        ) : null}
      </dl>

      {pending ? (
        <div className="mt-4 flex justify-end border-t border-hairline pt-4">
          <ReviewActions
            busy={busy}
            approveLabel="Approve"
            onApprove={onApprove}
            onReject={onReject}
          />
        </div>
      ) : null}
    </Panel>
  );
}
