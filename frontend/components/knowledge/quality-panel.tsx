"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, Play, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { KnowledgeAccessError } from "@/lib/knowledge/knowledge";
import {
  createQualityAssertion,
  fetchQualityAssertions,
  runQualityChecks,
  toggleQualityAssertion,
  type AssertionType,
  type QualityAssertion,
  type QualityStatus,
} from "@/lib/knowledge/quality";

/**
 * What a reviewer has asserted about the data itself, and what those checks
 * last found.
 *
 * The system already knows whether its SQL is correct; this is the other half.
 * A check names a table, a column and a threshold — never a connection.
 */
interface QualityPanelProps {
  dataSourceId: string;
}

const TONE: Record<QualityStatus, "default" | "secondary" | "destructive"> = {
  HEALTHY: "default",
  WARNING: "secondary",
  UNKNOWN: "secondary",
  STALE: "destructive",
  FAILING: "destructive",
};

const CONFIG_HINT: Record<AssertionType, string> = {
  FRESHNESS: "max_age_minutes",
  ROW_COUNT: "min_rows",
  NULL_RATE: "max_ratio",
  UNIQUE: "(no configuration)",
  ACCEPTED_VALUES: "values, comma separated",
  CUSTOM_SAFE_SQL: "not creatable here",
};

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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          {unhealthy.length > 0 ? (
            <>
              <Activity className="size-4 text-warning" aria-hidden="true" />
              {unhealthy.length} of {assertions.length} checks are unhealthy.
            </>
          ) : assertions.length > 0 ? (
            <>
              <ShieldCheck className="size-4 text-success" aria-hidden="true" />
              All {assertions.length} checks are healthy or unknown.
            </>
          ) : (
            "No quality assertions for this database yet."
          )}
        </p>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => setAdding((open) => !open)}>
            Add assertion
          </Button>
          <Button
            size="sm"
            disabled={busy || assertions.length === 0}
            onClick={() => void runAll()}
          >
            <Play className="size-3.5" aria-hidden="true" />
            {busy ? "Checking…" : "Run checks"}
          </Button>
        </div>
      </div>

      {error !== null ? (
        <p className="rounded-lg border border-border p-3 text-sm text-muted-foreground">
          {error}
        </p>
      ) : null}

      {adding ? (
        <div className="grid gap-2 rounded-lg border border-border p-4 sm:grid-cols-2">
          <Field label="Name" value={draft.name} onChange={(v) => setDraft({ ...draft, name: v })} />
          <label className="text-[11px] text-muted-foreground">
            Type
            <select
              value={draft.assertionType}
              onChange={(event) =>
                setDraft({ ...draft, assertionType: event.target.value as AssertionType })
              }
              className="mt-1 w-full rounded border border-border bg-transparent px-2 py-1.5 text-[13px] text-foreground"
            >
              {(
                ["FRESHNESS", "ROW_COUNT", "NULL_RATE", "UNIQUE", "ACCEPTED_VALUES"] as AssertionType[]
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
          <div className="sm:col-span-2">
            <Button size="sm" disabled={busy} onClick={() => void create()}>
              Save assertion
            </Button>
          </div>
        </div>
      ) : null}

      <ul className="space-y-2">
        {assertions.map((assertion) => (
          <li
            key={assertion.id}
            className="flex items-start justify-between gap-3 rounded-lg border border-border p-3"
          >
            <div className="min-w-0">
              <p className="text-sm font-medium">
                {assertion.name}
                {!assertion.enabled ? (
                  <span className="ms-2 text-xs text-muted-foreground">disabled</span>
                ) : null}
              </p>
              <p className="font-mono text-xs text-muted-foreground">
                {assertion.table}
                {assertion.columnName !== null ? `.${assertion.columnName}` : ""} ·{" "}
                {assertion.assertionType}
              </p>
              {assertion.detail !== null ? (
                <p className="mt-1 text-xs text-muted-foreground">{assertion.detail}</p>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Badge variant={TONE[assertion.status]}>{assertion.status}</Badge>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => void toggle(assertion)}
              >
                {assertion.enabled ? "Disable" : "Enable"}
              </Button>
            </div>
          </li>
        ))}
      </ul>
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
    <label className="text-[11px] text-muted-foreground">
      {label}
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded border border-border bg-transparent px-2 py-1.5 text-[13px] text-foreground"
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
