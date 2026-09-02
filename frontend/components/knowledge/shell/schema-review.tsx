"use client";

import { useMemo, useState } from "react";
import { motion } from "motion/react";
import {
  ArrowRight,
  Check,
  ChevronRight,
  Info,
  KeyRound,
  Pencil,
  Shapes,
  X,
} from "lucide-react";

import {
  DetailRow,
  EmptyState,
  Panel,
  StatusBadge,
} from "@/components/knowledge/shell/primitives";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { DUR, EASE_ENTRANCE } from "@/lib/motion";
import type { SemanticProposal } from "@/lib/knowledge/knowledge";
import { cn } from "@/lib/utils";

/**
 * Schema review, read as business meaning rather than as database notation.
 *
 * The person doing this job decides what a column *means*; they are not
 * required to know that the column is called `analytics.employees.arabic_name`.
 * So the concept leads, a few real values from the column sit under it as
 * evidence, and the physical path is one disclosure away for when somebody
 * does want to check it.
 *
 * Proposals are grouped under the concept they belong to, because reviewing
 * "everything Employee means" is one decision made twelve times, while an
 * undifferentiated list of eighty-six cards is eighty-six unrelated ones.
 */

interface SchemaReviewProps {
  proposals: readonly SemanticProposal[];
  /** Example values keyed by `schema.table.column`. Missing is normal. */
  previews: Readonly<Record<string, readonly string[]>>;
  drafts: Readonly<Record<string, string>>;
  busyId: string | null;
  onDraftChange: (id: string, value: string) => void;
  onReview: (id: string, action: "approve" | "reject") => void;
  onApproveMany: (ids: readonly string[]) => Promise<void>;
}

interface ReviewGroup {
  key: string;
  title: string;
  /** The concept's own proposal, when it is itself awaiting review. */
  entity: SemanticProposal | null;
  items: SemanticProposal[];
}

const RELATIONSHIPS = "__relationships__";

function group(proposals: readonly SemanticProposal[]): ReviewGroup[] {
  const groups = new Map<string, ReviewGroup>();

  const ensure = (key: string, title: string): ReviewGroup => {
    const existing = groups.get(key);
    if (existing !== undefined) return existing;
    const created: ReviewGroup = {
      key,
      title,
      entity: null,
      items: [],
    };
    groups.set(key, created);
    return created;
  };

  for (const proposal of proposals) {
    if (proposal.kind === "relationship") {
      ensure(RELATIONSHIPS, "Relationships").items.push(proposal);
      continue;
    }
    const name = proposal.entityName ?? "Unassigned";
    const bucket = ensure(name, name);
    if (proposal.kind === "entity") {
      bucket.entity = proposal;
    } else {
      bucket.items.push(proposal);
    }
  }

  for (const bucket of groups.values()) {
    // What identifies the concept comes before what merely describes it.
    bucket.items.sort((left, right) =>
      left.isIdentifier === right.isIdentifier ? 0 : left.isIdentifier ? -1 : 1,
    );
  }

  return [...groups.values()].sort((left, right) => {
    if (left.key === RELATIONSHIPS) return 1;
    if (right.key === RELATIONSHIPS) return -1;
    return left.title.localeCompare(right.title);
  });
}

function count(bucket: ReviewGroup): number {
  return bucket.items.length + (bucket.entity === null ? 0 : 1);
}

/* ------------------------------------------------------------------------- */

export function SchemaReview({
  proposals,
  previews,
  drafts,
  busyId,
  onDraftChange,
  onReview,
  onApproveMany,
}: SchemaReviewProps) {
  const groups = useMemo(() => group(proposals), [proposals]);
  const [opened, setOpened] = useState<Set<string> | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Eighty-six proposals expanded at once is a wall. The first concept opens
  // so the screen is never empty; the rest are one click each.
  const open = opened ?? new Set(groups.slice(0, 1).map((entry) => entry.key));

  const toggleGroup = (key: string) => {
    setOpened((current) => {
      const next = new Set(current ?? open);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const toggleSelected = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const reviewOne = (id: string, action: "approve" | "reject") => {
    setSelected((current) => {
      const next = new Set(current);
      next.delete(id);
      return next;
    });
    onReview(id, action);
  };

  const approveSelected = async () => {
    const ids = [...selected];
    await onApproveMany(ids);
    setSelected((current) => {
      const next = new Set(current);
      for (const id of ids) next.delete(id);
      return next;
    });
  };

  if (proposals.length === 0) {
    return (
      <EmptyState
        icon={Shapes}
        title="Nothing awaiting review"
        description="Scan a data source to discover what its tables mean. Proposals appear here for a person to confirm."
      />
    );
  }

  return (
    // Its own provider, so the section works wherever it is mounted rather
    // than only under the one the app root happens to supply.
    <TooltipProvider delayDuration={200}>
      <div className="flex flex-col gap-4">
        {selected.size > 0 ? (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: DUR.fast, ease: EASE_ENTRANCE }}
            className="sticky top-[88px] z-10 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/30 bg-surface-raised px-4 py-3 shadow-float"
          >
            <p className="text-[13px] text-foreground">
              <span className="font-medium tabular-nums">{selected.size}</span>{" "}
              selected. Each is approved individually and recorded on its own.
            </p>
            <div className="flex shrink-0 gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setSelected(new Set())}
              >
                Clear
              </Button>
              <Button
                size="sm"
                disabled={busyId !== null}
                onClick={() => {
                  void approveSelected();
                }}
              >
                <Check data-icon="inline-start" aria-hidden="true" />
                Approve selected
              </Button>
            </div>
          </motion.div>
        ) : null}

        {groups.map((bucket) => {
          const isOpen = open.has(bucket.key);
          const proposalCount = count(bucket);
          return (
            <Collapsible
              key={bucket.key}
              open={isOpen}
              onOpenChange={() => toggleGroup(bucket.key)}
            >
              <section>
                <CollapsibleTrigger asChild>
                  <button
                    type="button"
                    aria-label={`${bucket.title}, ${proposalCount} ${proposalCount === 1 ? "proposal" : "proposals"}`}
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-start",
                      "outline-none transition-colors hover:bg-surface",
                      "focus-visible:ring-[3px] focus-visible:ring-ring/50",
                    )}
                  >
                    <ChevronRight
                      className={cn(
                        "size-4 shrink-0 text-muted-foreground transition-transform duration-200",
                        isOpen && "rotate-90",
                      )}
                      aria-hidden="true"
                    />
                    <span className="text-[15px] font-medium text-foreground">
                      {bucket.title}
                    </span>
                    <span className="ms-auto rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
                      {proposalCount} {proposalCount === 1 ? "proposal" : "proposals"}
                    </span>
                  </button>
                </CollapsibleTrigger>

                <CollapsibleContent asChild>
                  <motion.div
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: DUR.fast, ease: EASE_ENTRANCE }}
                    className="mt-2 flex flex-col gap-3 ps-2"
                  >
                    {bucket.entity !== null ? (
                      <ProposalCard
                        proposal={bucket.entity}
                        preview={undefined}
                        draft={drafts[bucket.entity.id] ?? ""}
                        busy={busyId === bucket.entity.id}
                        selected={false}
                        selectable={false}
                        onSelect={() => undefined}
                        onDraftChange={onDraftChange}
                        onReview={reviewOne}
                      />
                    ) : null}
                    {bucket.items.map((proposal) => (
                      <ProposalCard
                        key={proposal.id}
                        proposal={proposal}
                        preview={previews[proposal.physical]}
                        draft={drafts[proposal.id] ?? ""}
                        busy={busyId === proposal.id}
                        selected={selected.has(proposal.id)}
                        selectable={proposal.kind === "attribute"}
                        onSelect={toggleSelected}
                        onDraftChange={onDraftChange}
                        onReview={reviewOne}
                      />
                    ))}
                  </motion.div>
                </CollapsibleContent>
              </section>
            </Collapsible>
          );
        })}
      </div>
    </TooltipProvider>
  );
}

/* ------------------------------------------------------------------------- */

const KIND_LABEL: Record<SemanticProposal["kind"], string> = {
  entity: "ENTITY",
  attribute: "ATTRIBUTE",
  relationship: "RELATIONSHIP",
};

function ProposalCard({
  proposal,
  preview,
  draft,
  busy,
  selected,
  selectable,
  onSelect,
  onDraftChange,
  onReview,
}: {
  proposal: SemanticProposal;
  preview: readonly string[] | undefined;
  draft: string;
  busy: boolean;
  selected: boolean;
  selectable: boolean;
  onSelect: (id: string) => void;
  onDraftChange: (id: string, value: string) => void;
  onReview: (id: string, action: "approve" | "reject") => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const corrected = draft.trim();

  return (
    <Panel
      interactive
      className={cn("p-5", selected && "border-primary/40 bg-surface-raised")}
    >
      <div className="flex items-start gap-3">
        {selectable ? (
          <Checkbox
            checked={selected}
            onCheckedChange={() => onSelect(proposal.id)}
            aria-label={`Select ${proposal.proposedConcept} for bulk approval`}
            className="mt-1"
          />
        ) : null}

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-2">
            {proposal.kind === "relationship" ? (
              <Relationship proposal={proposal} />
            ) : (
              <h4 className="text-[16px] font-medium text-foreground">
                {corrected === "" ? proposal.proposedConcept : corrected}
              </h4>
            )}
            <StatusBadge tone="neutral">{KIND_LABEL[proposal.kind]}</StatusBadge>
            {proposal.isIdentifier ? (
              <CanonicalKey entity={proposal.entityName} />
            ) : null}
            <Confidence value={proposal.confidence} />
          </div>

          {proposal.detail !== "" ? (
            <p className="measure mt-2 text-[13px] leading-relaxed text-muted-foreground">
              {proposal.detail}
            </p>
          ) : null}

          {proposal.kind === "attribute" ? (
            <ExampleValues values={preview} />
          ) : null}

          {renaming ? (
            <div className="mt-4">
              <label
                htmlFor={`edit-${proposal.id}`}
                className="text-[12.5px] font-medium text-muted-foreground"
              >
                Business meaning
                <span className="sr-only"> for {proposal.physical}</span>
              </label>
              <input
                id={`edit-${proposal.id}`}
                value={draft}
                autoFocus
                placeholder={proposal.proposedConcept}
                onChange={(event) =>
                  onDraftChange(proposal.id, event.target.value)
                }
                className="mt-1.5 h-8 w-full max-w-md rounded-lg border border-border bg-background px-2.5 text-[13px] outline-none transition-colors placeholder:text-muted-foreground/60 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              />
            </div>
          ) : null}

          <div className="mt-4 flex flex-wrap items-center justify-end gap-1.5">
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => onReview(proposal.id, "reject")}
              className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            >
              <X data-icon="inline-start" aria-hidden="true" />
              Reject
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              aria-expanded={renaming}
              onClick={() => {
                if (renaming) onDraftChange(proposal.id, "");
                setRenaming((current) => !current);
              }}
            >
              <Pencil data-icon="inline-start" aria-hidden="true" />
              {renaming ? "Cancel edit" : "Edit"}
            </Button>
            <Button
              size="sm"
              disabled={busy}
              onClick={() => onReview(proposal.id, "approve")}
            >
              <Check data-icon="inline-start" aria-hidden="true" />
              {corrected === "" ? "Approve" : "Save & approve"}
            </Button>
          </div>

          <TechnicalDetails proposal={proposal} />
        </div>
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------------- */

/** "Employee belongs to Department", with the join kept for the disclosure. */
function Relationship({ proposal }: { proposal: SemanticProposal }) {
  if (proposal.fromEntity === null || proposal.toEntity === null) {
    return (
      <h4 className="text-[16px] font-medium text-foreground">
        {proposal.proposedConcept}
      </h4>
    );
  }
  return (
    <h4 className="flex flex-wrap items-center gap-2 text-[16px] font-medium text-foreground">
      {proposal.fromEntity}{" "}
      <span className="inline-flex items-center gap-1.5 text-[13px] font-normal text-muted-foreground">
        <ArrowRight className="size-3.5" aria-hidden="true" />
        {proposal.proposedConcept}
        <ArrowRight className="size-3.5" aria-hidden="true" />
      </span>{" "}
      {proposal.toEntity}
    </h4>
  );
}

function Hint({ label, text }: { label: string; text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          className="rounded-full outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <Info className="size-3 text-current opacity-70" aria-hidden="true" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-64">{text}</TooltipContent>
    </Tooltip>
  );
}

function CanonicalKey({ entity }: { entity: string | null }) {
  const subject = entity ?? "record";
  return (
    <StatusBadge tone="accent">
      <KeyRound className="size-3" aria-hidden="true" />
      Canonical key
      <Hint
        label="What a canonical key is"
        text={`Stable identifier used to distinguish one ${subject} from another.`}
      />
    </StatusBadge>
  );
}

/**
 * Model confidence, kept small on purpose.
 *
 * It says how sure the model is about the *meaning* it proposed. It says
 * nothing about whether the data is any good, and a reviewer who reads it as a
 * quality score will approve the wrong things.
 */
function Confidence({ value }: { value: number | null }) {
  if (value === null) return null;
  return (
    <span className="ms-auto inline-flex shrink-0 items-center gap-1 text-[12px] tabular-nums text-muted-foreground">
      {Math.round(value * 100)}%
      <Hint
        label="What this percentage means"
        text="AI confidence in this proposed meaning. Not a measure of data quality."
      />
    </span>
  );
}

/**
 * A few real values from the column.
 *
 * Labelled "Example values" and nowhere called possible, complete or canonical
 * ones: the backend returns a bounded sample and returns nothing at all for a
 * column with many distinct values, so treating this as the set of values a
 * column can hold would be wrong.
 */
function ExampleValues({ values }: { values: readonly string[] | undefined }) {
  return (
    <div className="mt-4">
      <p className="label-xs text-muted-foreground">Example values</p>
      {values === undefined || values.length === 0 ? (
        <p className="mt-1.5 text-[13px] text-muted-foreground/70">
          No preview available
        </p>
      ) : (
        <ul className="mt-1.5 flex flex-wrap gap-1.5">
          {values.map((value) => (
            <li
              key={value}
              dir="auto"
              className="rounded-md bg-muted px-2 py-1 text-[12.5px] text-foreground"
            >
              {value}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TechnicalDetails({ proposal }: { proposal: SemanticProposal }) {
  return (
    <details className="group mt-4 border-t border-hairline pt-3">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-md text-[12.5px] text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50">
        <ChevronRight
          className="size-3.5 transition-transform group-open:rotate-90"
          aria-hidden="true"
        />
        Technical details
      </summary>
      <dl className="mt-2.5">
        {proposal.schemaName !== null ? (
          <DetailRow label="Schema" value={proposal.schemaName} mono />
        ) : null}
        {proposal.tableName !== null ? (
          <DetailRow label="Table" value={proposal.tableName} mono />
        ) : null}
        {proposal.columnName !== null ? (
          <DetailRow label="Column" value={proposal.columnName} mono />
        ) : null}
        {proposal.dataType !== null ? (
          <DetailRow label="Type" value={proposal.dataType} mono />
        ) : null}
        <DetailRow
          label={proposal.kind === "relationship" ? "Join" : "Full physical path"}
          value={proposal.physical}
          mono
        />
      </dl>
    </details>
  );
}
