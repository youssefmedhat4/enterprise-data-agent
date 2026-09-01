"use client";

import { useCallback, useEffect, useState } from "react";
import { CalendarClock, Check, Clock } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { KnowledgeAccessError } from "@/lib/knowledge/knowledge";
import {
  fetchTemporalDimensions,
  fetchTimePolicy,
  previewTimePhrase,
  saveTimePolicy,
  type FiscalYearLabel,
  type TemporalDimension,
  type TimePolicy,
  type TimePreview,
  type WeekStart,
} from "@/lib/knowledge/time";

/**
 * This database's calendar, and the columns that carry time.
 *
 * Everything is structured: a timezone, a week start, a fiscal start, a naming
 * convention. The preview resolves a phrase without answering a question, so a
 * reviewer can see that "fiscal YTD" means July here before an answer depends
 * on it — and it costs no model call.
 */
interface TimePanelProps {
  dataSourceId: string;
}

const WEEKDAYS: WeekStart[] = [
  "MONDAY",
  "TUESDAY",
  "WEDNESDAY",
  "THURSDAY",
  "FRIDAY",
  "SATURDAY",
  "SUNDAY",
];

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** Common zones. Any valid IANA name is accepted; the backend validates it. */
const ZONES = [
  "UTC",
  "Africa/Cairo",
  "Asia/Riyadh",
  "Europe/London",
  "America/New_York",
];

export function TimePanel({ dataSourceId }: TimePanelProps) {
  const [policy, setPolicy] = useState<TimePolicy | null>(null);
  const [dimensions, setDimensions] = useState<TemporalDimension[]>([]);
  const [phrase, setPhrase] = useState("fiscal YTD");
  const [preview, setPreview] = useState<TimePreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextPolicy, nextDimensions] = await Promise.all([
        fetchTimePolicy(dataSourceId),
        fetchTemporalDimensions(dataSourceId),
      ]);
      setPolicy(nextPolicy);
      setDimensions(nextDimensions);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof KnowledgeAccessError
          ? caught.message
          : "The time intelligence service is unavailable.",
      );
    }
  }, [dataSourceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    if (policy === null) return;
    setBusy(true);
    try {
      setPolicy(await saveTimePolicy(dataSourceId, policy));
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof KnowledgeAccessError
          ? caught.message
          : "The calendar could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  };

  const runPreview = async () => {
    setBusy(true);
    try {
      setPreview(await previewTimePhrase(dataSourceId, phrase));
      setError(null);
    } catch {
      setError("That phrase could not be previewed.");
    } finally {
      setBusy(false);
    }
  };

  if (error !== null && policy === null) {
    return (
      <p className="rounded-lg border border-border p-4 text-sm text-muted-foreground">
        {error}
      </p>
    );
  }
  if (policy === null) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-5">
      <section className="space-y-3 rounded-lg border border-border p-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-medium">
            <CalendarClock className="size-4 text-muted-foreground" aria-hidden="true" />
            Time policy
          </h2>
          <Badge variant={policy.status === "CONFIRMED" ? "default" : "secondary"}>
            {policy.status}
          </Badge>
        </div>
        {policy.status !== "CONFIRMED" ? (
          <p className="text-xs text-muted-foreground">
            Nobody has confirmed this calendar. Calendar periods still work;
            fiscal questions are declined rather than answered from an assumed
            January start.
          </p>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-[11px] text-muted-foreground">
            Time zone
            <input
              list="time-zones"
              value={policy.timezone}
              onChange={(event) =>
                setPolicy({ ...policy, timezone: event.target.value })
              }
              className="mt-1 w-full rounded border border-border bg-transparent px-2 py-1.5 text-[13px] text-foreground"
            />
            <datalist id="time-zones">
              {ZONES.map((zone) => (
                <option key={zone} value={zone} />
              ))}
            </datalist>
          </label>
          <label className="text-[11px] text-muted-foreground">
            Week starts
            <select
              value={policy.weekStart}
              onChange={(event) =>
                setPolicy({ ...policy, weekStart: event.target.value as WeekStart })
              }
              className="mt-1 w-full rounded border border-border bg-transparent px-2 py-1.5 text-[13px] text-foreground"
            >
              {WEEKDAYS.map((day) => (
                <option key={day} value={day}>
                  {day.charAt(0) + day.slice(1).toLowerCase()}
                </option>
              ))}
            </select>
          </label>
          <label className="text-[11px] text-muted-foreground">
            Fiscal year starts
            <select
              value={policy.fiscalYearStartMonth}
              onChange={(event) =>
                setPolicy({
                  ...policy,
                  fiscalYearStartMonth: Number(event.target.value),
                })
              }
              className="mt-1 w-full rounded border border-border bg-transparent px-2 py-1.5 text-[13px] text-foreground"
            >
              {MONTHS.map((month, index) => (
                <option key={month} value={index + 1}>
                  {month}
                </option>
              ))}
            </select>
          </label>
          <label className="text-[11px] text-muted-foreground">
            Fiscal year named after
            <select
              value={policy.fiscalYearLabel}
              onChange={(event) =>
                setPolicy({
                  ...policy,
                  fiscalYearLabel: event.target.value as FiscalYearLabel,
                })
              }
              className="mt-1 w-full rounded border border-border bg-transparent px-2 py-1.5 text-[13px] text-foreground"
            >
              <option value="START_YEAR">the year it starts</option>
              <option value="END_YEAR">the year it ends</option>
            </select>
          </label>
        </div>
        <p className="text-xs text-muted-foreground">
          {policy.fiscalYearLabel === "END_YEAR"
            ? `A year running ${MONTHS[policy.fiscalYearStartMonth - 1]} 2026 to ${MONTHS[policy.fiscalYearStartMonth - 1]} 2027 is called FY2027 here.`
            : `A year running ${MONTHS[policy.fiscalYearStartMonth - 1]} 2026 to ${MONTHS[policy.fiscalYearStartMonth - 1]} 2027 is called FY2026 here.`}
        </p>
        <Button size="sm" disabled={busy} onClick={() => void save()}>
          <Check className="size-3.5" aria-hidden="true" />
          Confirm calendar
        </Button>
      </section>

      <section className="space-y-2">
        <h2 className="label-xs text-muted-foreground">Temporal dimensions</h2>
        {dimensions.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
            No date columns have been reviewed for this database yet. Until one
            is, time phrases are answered exactly as they were before.
          </p>
        ) : (
          <ul className="space-y-2">
            {dimensions.map((dimension) => (
              <li
                key={dimension.id}
                className="flex items-start justify-between gap-3 rounded-lg border border-border p-3"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium">
                    {dimension.concept}
                    {dimension.isDefaultForEntity ? (
                      <span className="ms-2 text-xs text-muted-foreground">
                        default for {dimension.entity}
                      </span>
                    ) : null}
                  </p>
                  <p className="font-mono text-xs text-muted-foreground">
                    {dimension.table}.{dimension.column} · {dimension.role} ·{" "}
                    {dimension.storage}
                  </p>
                </div>
                <Badge
                  variant={
                    dimension.status === "CONFIRMED"
                      ? "default"
                      : dimension.status === "STALE"
                        ? "destructive"
                        : "secondary"
                  }
                >
                  {dimension.status}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-2 rounded-lg border border-border p-4">
        <h2 className="flex items-center gap-2 text-sm font-medium">
          <Clock className="size-4 text-muted-foreground" aria-hidden="true" />
          What does a phrase mean here?
        </h2>
        <div className="flex flex-wrap gap-2">
          <input
            value={phrase}
            onChange={(event) => setPhrase(event.target.value)}
            placeholder="fiscal YTD"
            className="min-w-48 flex-1 rounded border border-border bg-transparent px-2 py-1.5 text-[13px] text-foreground"
          />
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void runPreview()}>
            Preview
          </Button>
        </div>
        {preview !== null ? (
          <p className="text-[13px] text-muted-foreground">
            {preview.recognised && preview.start !== ""
              ? preview.detail
              : preview.detail || "That phrase names no period."}
          </p>
        ) : null}
      </section>

      {error !== null ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : null}
    </div>
  );
}
