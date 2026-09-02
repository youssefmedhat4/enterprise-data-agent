"use client";

import { ArrowRight, Check, X } from "lucide-react";

import {
  DetailRow,
  Panel,
  StatusBadge,
  toneForStatus,
} from "@/components/knowledge/shell/primitives";
import { Button } from "@/components/ui/button";
import type {
  KnowledgeCandidate,
  SemanticProposal,
} from "@/lib/knowledge/knowledge";

/**
 * The two cards a person actually makes decisions on.
 *
 * Both follow the same rule: whatever is being approved has to be legible
 * *before* the buttons are reachable. Approve is the affirmative action and
 * looks like it; reject is available without shouting, because rejecting is
 * ordinary here rather than a failure; correcting a name comes first in reading
 * order because it is the thing a reviewer most often wants to do.
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

/**
 * A physical table or column, and what the system thinks it means.
 *
 * The arrow is the point of the card: this is a translation being proposed,
 * and the reviewer is confirming or correcting the right-hand side.
 */
export function SchemaProposalCard({
  proposal,
  draft,
  busy,
  onDraftChange,
  onApprove,
  onReject,
}: {
  proposal: SemanticProposal;
  draft: string;
  busy: boolean;
  onDraftChange: (value: string) => void;
  onApprove: () => void;
  onReject: () => void;
}) {
  const corrected = draft.trim();

  return (
    <Panel interactive className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <code className="rounded-md bg-muted px-2 py-1 font-mono text-[12px] text-muted-foreground">
            {proposal.physical}
          </code>
          <ArrowRight
            className="size-3.5 shrink-0 text-muted-foreground/60"
            aria-hidden="true"
          />
          <h3 className="text-[15px] font-medium text-foreground">
            {corrected === "" ? proposal.proposedConcept : corrected}
          </h3>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <StatusBadge tone="neutral">{proposal.kind}</StatusBadge>
          {proposal.confidence !== null ? (
            <StatusBadge
              tone={proposal.confidence >= 0.8 ? "positive" : "attention"}
            >
              {Math.round(proposal.confidence * 100)}%
            </StatusBadge>
          ) : null}
        </div>
      </div>

      {proposal.detail !== "" ? (
        <p className="measure mt-3 text-[13px] leading-relaxed text-muted-foreground">
          {proposal.detail}
        </p>
      ) : null}

      <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-hairline pt-4">
        <div className="min-w-56 flex-1">
          <label
            htmlFor={`edit-${proposal.id}`}
            className="text-[12.5px] font-medium text-muted-foreground"
          >
            Corrected meaning
            <span className="sr-only"> for {proposal.physical}</span>
          </label>
          <input
            id={`edit-${proposal.id}`}
            value={draft}
            placeholder={proposal.proposedConcept}
            onChange={(event) => onDraftChange(event.target.value)}
            className="mt-1.5 h-8 w-full rounded-lg border border-border bg-background px-2.5 text-[13px] outline-none transition-colors placeholder:text-muted-foreground/60 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
          />
        </div>
        <ReviewActions
          busy={busy}
          approveLabel={corrected ? "Save & approve" : "Approve"}
          onApprove={onApprove}
          onReject={onReject}
        />
      </div>
    </Panel>
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
