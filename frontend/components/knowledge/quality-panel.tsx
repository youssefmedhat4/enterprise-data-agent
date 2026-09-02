"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, Play, Plus, ShieldCheck } from "lucide-react";

import {
  EmptyState,
  Panel,
  StatusBadge,
  toneForStatus,
} from "@/components/knowledge/shell/primitives";
import { Button } from "@/components/ui/button";
import { KnowledgeAccessError } from "@/lib/knowledge/knowledge";
import {
  createQualityAssertion,
  fetchQualityAssertions,
  runQualityChecks,
  toggleQualityAssertion,
  type AssertionType,
  type QualityAssertion,
} from "@/lib/knowledge/quality";
import { cn } from "@/lib/utils";

/**
 * What a reviewer has asserted about the data itself, and what those checks
 * last found.
 *
 * The system already knows whether its SQL is correct; this is the other half.
 * A check names a table, a column and a threshold — never a connection. The
 * summary at the top is the whole point of the page: one sentence saying
 * whether anything is wrong, before any list of individual checks.
 */
interface QualityPanelProps {
  dataSourceId: string;
}

const CONFIG_HINT: Record<AssertionType, string> = {
  FRESHNESS: "max_age_minutes",
  ROW_COUNT: "min_rows",
  NULL_RATE: "max_ratio",
  UNIQUE: "(no configuration)",
  ACCEPTED_VALUES: "values, comma separated",
  CUSTOM_SAFE_SQL: "not creatable here",
};

const FIELD_CLASS =
  "mt-1.5 h-8 w-full rounded-lg border border-border bg-background px-2.5 text-[13px] text-foreground outline-none transition-colors focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50";

export function QualityPanel({ dataSourceId }: QualityPanelProps) {
  const [assertions, setAssertions] = useState<QualityAssertion[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({
    name: "",
    assertionType: "FRESHNESS" as AssertionType,
    schemaName: "",
    tableName: "",
    columnName: "",
    configuration: "",
  });

  const load = useCallback(async () => {
    try {
      setAssertions(await fetchQualityAssertions(dataSourceId));
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof KnowledgeAccessError
          ? caught.message
          : "The data quality service is unavailable.",
      );
    }
  }, [dataSourceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const runAll = async () => {
    setBusy(true);
    try {
      setAssertions(await runQualityChecks(dataSourceId));
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof KnowledgeAccessError
          ? caught.message
          : "The checks could not be run.",
      );
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (assertion: QualityAssertion) => {
    setBusy(true);
    try {
      await toggleQualityAssertion(dataSourceId, assertion.id);
      await load();
    } catch {
      setError("The assertion could not be updated.");
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    setBusy(true);
    try {
      await createQualityAssertion(dataSourceId, {
        name: draft.name,
        assertionType: draft.assertionType,
        schemaName: draft.schemaName,
        tableName: draft.tableName,
        columnName: draft.columnName || null,
        configuration: parseConfiguration(draft.assertionType, draft.configuration),
      });
      setAdding(false);
      setDraft({ ...draft, name: "", tableName: "", columnName: "", configuration: "" });
      await load();
    } catch (caught) {
      setError(
        caught instanceof KnowledgeAccessError
          ? caught.message
          : "The assertion could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  };

  const unhealthy = assertions.filter(
    (item) => item.status === "STALE" || item.status === "FAILING",
  );
  const warning = assertions.filter((item) => item.status === "WARNING");
  const healthy = assertions.length - unhealthy.length - warning.length;

  return (
    <div className="space-y-5">
      {/* -------------------------------------------------- health summary */}
      {assertions.length > 0 ? (
        <Panel
          className={cn(
            "flex flex-wrap items-center justify-between gap-4 p-5",
            unhealthy.length > 0 && "border-destructive/30 bg-destructive/8",
          )}
        >
          <div className="flex items-start gap-3">
            {unhealthy.length > 0 ? (
              <Activity
                className="mt-0.5 size-4 shrink-0 text-destructive"
                aria-hidden="true"
              />
            ) : (
              <ShieldCheck
                className="mt-0.5 size-4 shrink-0 text-success"
                aria-hidden="true"
              />
            )}
            <div>
              <p className="text-[14px] font-medium text-foreground">
                {unhealthy.length > 0
                  ? `${unhealthy.length} of ${assertions.length} checks are unhealthy.`
                  : `All ${assertions.length} checks are healthy or unknown.`}
              </p>
              <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted-foreground">
                <span>{healthy} passing</span>
                <span aria-hidden="true">·</span>
                <span>{warning.length} warning</span>
                <span aria-hidden="true">·</span>
                <span>{unhealthy.length} failing or stale</span>
              </p>
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setAdding((open) => !open)}
            >
              <Plus className="size-3.5" aria-hidden="true" />
              {adding ? "Cancel" : "Add assertion"}
            </Button>
            <Button size="sm" disabled={busy} onClick={() => void runAll()}>
              <Play className="size-3.5" aria-hidden="true" />
              {busy ? "Checking…" : "Run checks"}
            </Button>
          </div>
        </Panel>
      ) : (
        <div className="flex justify-end">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setAdding((open) => !open)}
          >
            <Plus className="size-3.5" aria-hidden="true" />
            {adding ? "Cancel" : "Add assertion"}
          </Button>
        </div>
      )}

      {error !== null ? (
        <Panel className="px-4 py-3 text-[13px] text-muted-foreground">
          {error}
        </Panel>
      ) : null}

      {adding ? (
        <Panel className="p-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Name"
              value={draft.name}
              onChange={(v) => setDraft({ ...draft, name: v })}
            />
            <label className="text-[12.5px] font-medium text-muted-foreground">
              Type
              <select
                value={draft.assertionType}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    assertionType: event.target.value as AssertionType,
                  })
                }
                className={FIELD_CLASS}
              >
                {(
                  [
                    "FRESHNESS",
                    "ROW_COUNT",
                    "NULL_RATE",
                    "UNIQUE",
                    "ACCEPTED_VALUES",
                  ] as AssertionType[]
                ).map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
            <Field
              label="Schema"
              value={draft.schemaName}
              onChange={(v) => setDraft({ ...draft, schemaName: v })}
            />
            <Field
              label="Table"
              value={draft.tableName}
              onChange={(v) => setDraft({ ...draft, tableName: v })}
            />
            <Field
              label="Column"
              value={draft.columnName}
              onChange={(v) => setDraft({ ...draft, columnName: v })}
            />
            <Field
              label={CONFIG_HINT[draft.assertionType]}
              value={draft.configuration}
              onChange={(v) => setDraft({ ...draft, configuration: v })}
            />
          </div>
          <Button
            size="sm"
            className="mt-4"
            disabled={busy}
            onClick={() => void create()}
          >
            Save assertion
          </Button>
        </Panel>
      ) : null}

      {assertions.length === 0 ? (
        <EmptyState
          icon={ShieldCheck}
          title="No quality assertions yet"
          description="An assertion names a table, a column and a threshold — freshness, row count, null rate, uniqueness or accepted values."
        />
      ) : (
        <ul className="space-y-3">
          {assertions.map((assertion) => (
            <li key={assertion.id}>
              <Panel
                interactive
                className="flex flex-wrap items-start justify-between gap-4 p-4"
              >
                <div className="min-w-0">
                  <p className="text-[13.5px] font-medium text-foreground">
                    {assertion.name}
                    {!assertion.enabled ? (
                      <span className="ms-2 text-[12px] font-normal text-muted-foreground">
                        disabled
                      </span>
                    ) : null}
                  </p>
                  <p className="mt-1 font-mono text-[12px] text-muted-foreground">
                    {assertion.table}
                    {assertion.columnName !== null
                      ? `.${assertion.columnName}`
                      : ""}{" "}
                    · {assertion.assertionType}
                  </p>
                  {assertion.detail !== null ? (
                    <p className="measure mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground">
                      {assertion.detail}
                    </p>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <StatusBadge tone={toneForStatus(assertion.status)} dot>
                    {assertion.status}
                  </StatusBadge>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => void toggle(assertion)}
                  >
                    {assertion.enabled ? "Disable" : "Enable"}
                  </Button>
                </div>
              </Panel>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="text-[12.5px] font-medium text-muted-foreground">
      {label}
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={FIELD_CLASS}
      />
    </label>
  );
}

/** The backend validates this too; here it only shapes what gets sent. */
function parseConfiguration(
  assertionType: AssertionType,
  raw: string,
): Record<string, unknown> {
  const trimmed = raw.trim();
  if (assertionType === "UNIQUE") return {};
  if (assertionType === "ACCEPTED_VALUES") {
    return {
      values: trimmed
        .split(",")
        .map((value) => value.trim())
        .filter((value) => value !== ""),
    };
  }
  const key =
    assertionType === "FRESHNESS"
      ? "max_age_minutes"
      : assertionType === "ROW_COUNT"
        ? "min_rows"
        : "max_ratio";
  return { [key]: Number(trimmed) };
}
