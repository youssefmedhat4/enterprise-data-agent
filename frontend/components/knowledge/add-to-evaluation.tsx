"use client";

import { useState } from "react";
import { ClipboardCheck, ClipboardPlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { createEvaluationCase, type Expectation } from "@/lib/knowledge/evaluation";
import { KnowledgeAccessError } from "@/lib/knowledge/knowledge";
import type { AnalyticsResponse } from "@/lib/types/analytics";

/**
 * Turn one answered question into a benchmark.
 *
 * The expected value is pre-filled from what the system just answered, and the
 * reviewer has to look at it and confirm. That is the whole point of the step:
 * a case recorded automatically only asserts that nothing changed, while a case
 * a person confirmed asserts that the answer is right. Those are different
 * claims, and only the second one is worth failing a release over.
 */
interface AddToEvaluationProps {
  question: string;
  response: AnalyticsResponse;
}

export function AddToEvaluation({ question, response }: AddToEvaluationProps) {
  const suggestion = suggest(response);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [expected, setExpected] = useState(suggestion.value);
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);

  if (response.status !== "completed") return null;

  const save = async () => {
    setState("saving");
    setError(null);
    try {
      await createEvaluationCase(response.data_source_id, {
        name: name.trim() || response.answer.slice(0, 60),
        question,
        expectation: suggestion.expectation,
        expected:
          suggestion.expectation === "EMPTY"
            ? {}
            : suggestion.expectation === "ROW_COUNT"
              ? { value: Number(expected) }
              : { value: expected },
      });
      setState("saved");
      setOpen(false);
    } catch (caught) {
      setState("idle");
      setError(
        caught instanceof KnowledgeAccessError
          ? caught.message
          : "The evaluation case could not be saved.",
      );
    }
  };

  if (state === "saved") {
    return (
      <p className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <ClipboardCheck className="size-3 text-success" aria-hidden="true" />
        Added to the evaluation set
      </p>
    );
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded text-[11px] text-muted-foreground transition-colors hover:text-foreground"
      >
        <ClipboardPlus className="size-3" aria-hidden="true" />
        Add to evaluation set
      </button>
    );
  }

  return (
    <div className="rounded-lg border border-border p-3">
      <p className="text-[13px] font-medium">Add to evaluation set</p>
      <p className="mt-1 text-[12px] text-muted-foreground">
        Confirm the answer below is the one this question should always give.
      </p>
      <div className="mt-3 space-y-2">
        <label className="block text-[11px] text-muted-foreground">
          Name
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Active headcount"
            className="mt-1 w-full rounded border border-border bg-transparent px-2 py-1.5 text-[13px] text-foreground"
          />
        </label>
        <label className="block text-[11px] text-muted-foreground">
          {suggestion.expectation === "ROW_COUNT" ? "Expected row count" : "Expected value"}
          <input
            value={expected}
            onChange={(event) => setExpected(event.target.value)}
            disabled={suggestion.expectation === "EMPTY"}
            className="mt-1 w-full rounded border border-border bg-transparent px-2 py-1.5 font-mono text-[13px] text-foreground disabled:opacity-50"
          />
        </label>
      </div>
      {error !== null ? (
        <p className="mt-2 text-[12px] text-muted-foreground">{error}</p>
      ) : null}
      <div className="mt-3 flex gap-2">
        <Button size="sm" disabled={state === "saving"} onClick={() => void save()}>
          {state === "saving" ? "Saving…" : "Save case"}
        </Button>
        <Button size="sm" variant="outline" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/**
 * What kind of comparison this answer supports.
 *
 * A single cell is a number to check. Anything wider is offered as a row count,
 * because a reviewer confirming a whole table by hand in a popover is not a
 * benchmark anyone will keep accurate.
 */
function suggest(response: AnalyticsResponse): {
  expectation: Expectation;
  value: string;
} {
  const rows = response.rows;
  if (rows.length === 0) return { expectation: "EMPTY", value: "" };
  const first = rows[0];
  const keys = Object.keys(first ?? {});
  if (rows.length === 1 && keys.length === 1 && first !== undefined) {
    return { expectation: "SCALAR", value: String(first[keys[0] as string] ?? "") };
  }
  return { expectation: "ROW_COUNT", value: String(rows.length) };
}
