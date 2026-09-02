"use client";

import { useCallback, useEffect, useState } from "react";
import { CalendarClock, Check, Clock } from "lucide-react";

import {
  EmptyState,
  Panel,
  Skeleton,
  StatusBadge,
  toneForStatus,
} from "@/components/knowledge/shell/primitives";
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
 * convention. Presented as a decision a person makes rather than a set of
 * knobs — each field says what it changes, and the sentence underneath spells
 * out the consequence in the same words a reader would use.
 *
 * The preview resolves a phrase without answering a question, so a reviewer can
 * see that "fiscal YTD" means July here before an answer depends on it — and it
 * costs no model call.
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

const FIELD_CLASS =
  "mt-1.5 h-8 w-full rounded-lg border border-border bg-background px-2.5 text-[13px] text-foreground outline-none transition-colors focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50";

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
      <Panel className="px-4 py-3 text-[13px] text-muted-foreground">
        {error}
      </Panel>
    );
  }
  if (policy === null) {
    return (
      <div className="space-y-5">
        <Panel className="space-y-4 p-5">
          <Skeleton className="h-4 w-32" />
          <div className="grid gap-4 sm:grid-cols-2">
            {[0, 1, 2, 3].map((index) => (
              <Skeleton key={index} className="h-8 w-full" />
            ))}
          </div>
        </Panel>
      </div>
    );
  }

  const fiscalMonth = MONTHS[policy.fiscalYearStartMonth - 1];

  return (
    <div className="space-y-6">
      {/* --------------------------------------------------------- calendar */}
      <Panel className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="flex items-center gap-2 text-[14px] font-medium text-foreground">
            <CalendarClock
              className="size-4 text-muted-foreground"
              aria-hidden="true"
            />
            Calendar
          </h3>
          {/* An unconfirmed calendar is not neutral: it is why a fiscal
              question gets declined, so it reads as something to act on. */}
          <StatusBadge
            tone={policy.status === "CONFIRMED" ? "positive" : "attention"}
            dot
          >
            {policy.status}
          </StatusBadge>
        </div>

        {policy.status !== "CONFIRMED" ? (
          <p className="measure mt-3 text-[12.5px] leading-relaxed text-muted-foreground">
            Nobody has confirmed this calendar. Calendar periods still work;
            fiscal questions are declined rather than answered from an assumed
            January start.
          </p>
        ) : null}

        <div className="mt-4 grid gap-4 border-t border-hairline pt-4 sm:grid-cols-2">
          <label className="text-[12.5px] font-medium text-muted-foreground">
            Time zone
            <input
              list="time-zones"
              value={policy.timezone}
              onChange={(event) =>
                setPolicy({ ...policy, timezone: event.target.value })
              }
              className={FIELD_CLASS}
            />
            <datalist id="time-zones">
              {ZONES.map((zone) => (
                <option key={zone} value={zone} />
              ))}
            </datalist>
          </label>
          <label className="text-[12.5px] font-medium text-muted-foreground">
            Week starts
            <select
              value={policy.weekStart}
              onChange={(event) =>
                setPolicy({ ...policy, weekStart: event.target.value as WeekStart })
              }
              className={FIELD_CLASS}
            >
              {WEEKDAYS.map((day) => (
                <option key={day} value={day}>
                  {day.charAt(0) + day.slice(1).toLowerCase()}
                </option>
              ))}
            </select>
          </label>
          <label className="text-[12.5px] font-medium text-muted-foreground">
            Fiscal year starts
            <select
              value={policy.fiscalYearStartMonth}
              onChange={(event) =>
                setPolicy({
                  ...policy,
                  fiscalYearStartMonth: Number(event.target.value),
                })
              }
              className={FIELD_CLASS}
            >
              {MONTHS.map((month, index) => (
                <option key={month} value={index + 1}>
                  {month}
                </option>
              ))}
            </select>
          </label>
          <label className="text-[12.5px] font-medium text-muted-foreground">
            Fiscal year named after
            <select
              value={policy.fiscalYearLabel}
              onChange={(event) =>
                setPolicy({
                  ...policy,
                  fiscalYearLabel: event.target.value as FiscalYearLabel,
                })
              }
              className={FIELD_CLASS}
            >
              <option value="START_YEAR">the year it starts</option>
              <option value="END_YEAR">the year it ends</option>
            </select>
          </label>
        </div>

        {/* The consequence, stated the way someone would say it aloud. */}
        <p className="measure mt-4 rounded-lg bg-muted/50 px-3.5 py-2.5 text-[12.5px] leading-relaxed text-muted-foreground">
          A year running {fiscalMonth} 2026 to {fiscalMonth} 2027 is called{" "}
          <span className="font-medium text-foreground">
            {policy.fiscalYearLabel === "END_YEAR" ? "FY2027" : "FY2026"}
          </span>{" "}
          here.
        </p>

        <Button size="sm" className="mt-4" disabled={busy} onClick={() => void save()}>
          <Check className="size-3.5" aria-hidden="true" />
          Confirm calendar
        </Button>
      </Panel>

      {/* ----------------------------------------------- temporal dimensions */}
      <section className="space-y-3">
        <h3 className="text-[14px] font-medium text-foreground">
          Temporal dimensions
        </h3>
        {dimensions.length === 0 ? (
          <EmptyState
            icon={Clock}
            title="No date columns reviewed yet"
            description="Until one is confirmed, time phrases are answered exactly as they were before."
          />
        ) : (
          <ul className="space-y-3">
            {dimensions.map((dimension) => (
              <li key={dimension.id}>
                <Panel
                  interactive
                  className="flex flex-wrap items-start justify-between gap-4 p-4"
                >
                  <div className="min-w-0">
                    <p className="text-[13.5px] font-medium text-foreground">
                      {dimension.concept}
                      {dimension.isDefaultForEntity ? (
                        <span className="ms-2 text-[12px] font-normal text-muted-foreground">
                          default for {dimension.entity}
                        </span>
                      ) : null}
                    </p>
                    <p className="mt-1 font-mono text-[12px] text-muted-foreground">
                      {dimension.table}.{dimension.column} · {dimension.role} ·{" "}
                      {dimension.storage}
                    </p>
                  </div>
                  <StatusBadge tone={toneForStatus(dimension.status)} dot>
                    {dimension.status}
                  </StatusBadge>
                </Panel>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ---------------------------------------------------------- preview */}
      <Panel className="p-5">
        <h3 className="flex items-center gap-2 text-[14px] font-medium text-foreground">
          <Clock className="size-4 text-muted-foreground" aria-hidden="true" />
          What does a phrase mean here?
        </h3>
        <p className="measure mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground">
          Resolves a period against this calendar without asking a question of
          the database.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <label className="sr-only" htmlFor="time-phrase">
            Time phrase
          </label>
          <input
            id="time-phrase"
            value={phrase}
            onChange={(event) => setPhrase(event.target.value)}
            placeholder="fiscal YTD"
            className="h-8 min-w-56 flex-1 rounded-lg border border-border bg-background px-2.5 text-[13px] text-foreground outline-none transition-colors placeholder:text-muted-foreground/60 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
          />
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void runPreview()}
          >
            Preview
          </Button>
        </div>
        {preview !== null ? (
          <p className="measure mt-3 rounded-lg bg-muted/50 px-3.5 py-2.5 text-[13px] leading-relaxed text-foreground">
            {preview.recognised && preview.start !== ""
              ? preview.detail
              : preview.detail || "That phrase names no period."}
          </p>
        ) : null}
      </Panel>

      {error !== null ? (
        <p className="text-[13px] text-muted-foreground">{error}</p>
      ) : null}
    </div>
  );
}
