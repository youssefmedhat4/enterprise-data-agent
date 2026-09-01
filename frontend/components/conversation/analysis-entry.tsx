"use client";

import { motion } from "motion/react";
import { Check, Copy, HelpCircle, ShieldAlert, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";

import { EvidencePanel } from "@/components/analytics/evidence-panel";
import { AnswerTrace } from "@/components/analytics/answer-trace";
import { KpiRow } from "@/components/analytics/kpi-row";
import { ProvenanceStrip } from "@/components/provenance/provenance-strip";
import { formatGroundedNumbers } from "@/lib/format/values";
import { revealChild, revealParent } from "@/lib/motion";
import type { AnalyticsResponse } from "@/lib/types/analytics";

/**
 * One completed analysis, rendered as a document section rather than a chat
 * message: narrative first, then evidence, then the trust line.
 *
 * The backend's answer is never re-split, summarised, or recomputed. The only
 * transform applied is `formatGroundedNumbers`, which restyles a numeral solely
 * when its value also appears in the returned rows — so figures the model copied
 * out of the raw JSON (`710000.00`) read as `710,000` without any value
 * changing, and anything ungrounded is left byte-identical.
 */
interface AnalysisEntryProps {
  question: string;
  response: AnalyticsResponse;
  onOpenDetails: (response: AnalyticsResponse) => void;
  onAsk: (question: string) => void;
  disabled: boolean;
}

export function AnalysisEntry({
  question,
  response,
  onOpenDetails,
  onAsk,
  disabled,
}: AnalysisEntryProps) {
  const [copied, setCopied] = useState(false);

  // Display-only tidy-up of figures the model copied out of the raw result JSON.
  const answer = useMemo(
    () => formatGroundedNumbers(response.answer, response.rows),
    [response.answer, response.rows],
  );

  const copyAnswer = async () => {
    try {
      await navigator.clipboard.writeText(answer);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard unavailable in this context; the text remains selectable.
    }
  };

  if (response.status === "clarification_required") {
    const choices = response.clarification_choices ?? [];
    return (
      <StateNotice
        tone="info"
        icon={<HelpCircle className="size-3.5" aria-hidden="true" />}
        label="Needs clarification"
        body={clarificationPrompt(response)}
        footnote={
          choices.length > 0
            ? "Pick one, or answer below. Either way this analysis continues."
            : "Answer below and this analysis continues on the same thread."
        }
      >
        {choices.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {choices.map((choice) => (
              <button
                key={choice.value}
                type="button"
                disabled={disabled}
                onClick={() => onAsk(choice.value)}
                className="rounded-full border border-border px-3 py-1.5 text-[13px] text-foreground transition-colors hover:bg-muted disabled:pointer-events-none disabled:opacity-50"
              >
                {choice.label}
              </button>
            ))}
          </div>
        ) : null}
      </StateNotice>
    );
  }

  if (response.status === "blocked") {
    return (
      <StateNotice
        tone="warning"
        icon={<ShieldAlert className="size-3.5" aria-hidden="true" />}
        label="Not permitted"
        body={response.answer}
        footnote="This workspace is read-only and answers analytical questions about your data."
      />
    );
  }

  const isEmpty = response.status === "empty" || response.rows.length === 0;
  const singleRow = response.rows.length === 1 ? response.rows[0] : null;

  return (
    <motion.div
      variants={revealParent}
      initial="hidden"
      animate="visible"
      className="min-w-0 space-y-6"
    >
      {/* Headline figures lead when the result is a single row. */}
      {singleRow !== null ? (
        <motion.div variants={revealChild}>
          <KpiRow
            row={singleRow}
            columns={response.columns}
            columnTypes={response.provenance.result.column_types}
          />
        </motion.div>
      ) : null}

      {/* Analytical narrative. */}
      <motion.div variants={revealChild} className="group/answer">
        <p
          dir="auto"
          className="measure wrap-anywhere text-[17px] leading-[1.62] text-foreground"
        >
          {answer}
        </p>
        <button
          type="button"
          onClick={copyAnswer}
          className="mt-2 inline-flex items-center gap-1.5 rounded text-[11px] text-muted-foreground opacity-0 transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover/answer:opacity-100"
        >
          {copied ? (
            <Check className="size-3 text-success" aria-hidden="true" />
          ) : (
            <Copy className="size-3" aria-hidden="true" />
          )}
          {copied ? "Copied" : "Copy answer"}
        </button>
      </motion.div>


      {/* Evidence. */}
      {isEmpty ? (
        <motion.p
          variants={revealChild}
          className="rounded-lg border border-dashed border-border px-4 py-3.5 text-[13px] text-muted-foreground"
        >
          The query ran successfully but matched no rows. Try widening the time
          range or relaxing a filter.
        </motion.p>
      ) : (
        <motion.div variants={revealChild}>
          <EvidencePanel response={response} />
        </motion.div>
      )}

      {/* What the backend measured about the data behind this answer. The
          numbers above are unchanged: the query was correct, and this says the
          data underneath may not be. Never model-written. */}
      {response.data_quality.length > 0 ? (
        <motion.ul variants={revealChild} className="space-y-1.5">
          {response.data_quality.map((warning) => (
            <li
              key={`${warning.table}-${warning.message}`}
              className="flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/5 px-3 py-2 text-[13px]"
            >
              <TriangleAlert
                className="mt-0.5 size-3.5 shrink-0 text-warning"
                aria-hidden="true"
              />
              <span>
                <span className="font-medium">Data quality</span>
                <span className="text-muted-foreground"> · {warning.table}</span>
                <span className="block text-muted-foreground">{warning.message}</span>
              </span>
            </li>
          ))}
        </motion.ul>
      ) : null}

      {/* How this answer was produced, folded away until someone asks. The
          reviewer's route into the evaluation set lives inside it. */}
      <motion.div variants={revealChild}>
        <AnswerTrace question={question} response={response} />
      </motion.div>

      {response.warnings.length > 0 ? (
        <motion.ul variants={revealChild} className="space-y-1.5">
          {response.warnings.map((warning) => (
            <li
              key={warning}
              className="flex items-start gap-2 text-[12px] leading-relaxed"
            >
              <TriangleAlert
                className="mt-0.5 size-3.5 shrink-0 text-warning"
                aria-hidden="true"
              />
              <span className="text-foreground">{warning}</span>
            </li>
          ))}
        </motion.ul>
      ) : null}

      {/* Trust line. */}
      <motion.div variants={revealChild}>
        <ProvenanceStrip
          response={response}
          onOpenDetails={() => onOpenDetails(response)}
        />
      </motion.div>
    </motion.div>
  );
}

/** Clarification and block states share one restrained treatment. */
function StateNotice({
  tone,
  icon,
  label,
  body,
  footnote,
  children,
}: {
  tone: "info" | "warning";
  icon: React.ReactNode;
  label: string;
  body: string;
  footnote: string;
  children?: React.ReactNode;
}) {
  const accent = tone === "info" ? "text-info" : "text-warning";
  const bar = tone === "info" ? "bg-info" : "bg-warning";

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="relative ps-4"
    >
      <span
        aria-hidden="true"
        className={`absolute inset-y-0 start-0 w-[2px] rounded-full ${bar} opacity-70`}
      />
      <p className={`label-xs mb-2 flex items-center gap-1.5 ${accent}`}>
        {icon}
        {label}
      </p>
      <p
        dir="auto"
        className="measure wrap-anywhere text-[17px] leading-[1.6] text-foreground"
      >
        {body}
      </p>
      {children}
      <p className="mt-2 text-[12px] text-muted-foreground">{footnote}</p>
    </motion.div>
  );
}

/**
 * The question without its inline list of options.
 *
 * When the options are rendered as buttons, repeating them mid-sentence as
 * `OU2100 | Operations; OU2200 | Operations` reads like debug output. The
 * backend still sends the full sentence for clients that cannot show choices.
 */
function clarificationPrompt(response: AnalyticsResponse): string {
  const question = response.clarification_question ?? response.answer;
  if ((response.clarification_choices ?? []).length === 0) return question;
  const [stem] = question.split(":");
  return stem === undefined || stem.trim() === "" ? question : `${stem.trim()}?`;
}
