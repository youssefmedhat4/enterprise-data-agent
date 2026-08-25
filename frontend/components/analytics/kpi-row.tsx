import {
  classifyColumn,
  formatCell,
  humanizeColumn,
} from "@/lib/format/values";
import type { ResultRow } from "@/lib/types/analytics";

/**
 * Headline figures for a single-row result.
 *
 * When the backend returns exactly one row, the numbers *are* the answer and
 * should be readable without parsing a table. Deliberately not cards — figures
 * separated by hairlines are denser and calmer than boxes, and the scale jump
 * is what creates hierarchy.
 *
 * These are backend values formatted for display, never recomputed.
 */
interface KpiRowProps {
  row: ResultRow;
  columns: string[];
  columnTypes: Record<string, string>;
}

const MAX_FIGURES = 4;

export function KpiRow({ row, columns, columnTypes }: KpiRowProps) {
  const figures = columns
    .map((column) => ({
      column,
      kind: classifyColumn(column, columnTypes, [row]),
    }))
    .filter((entry) => entry.kind === "number")
    .slice(0, MAX_FIGURES);

  if (figures.length === 0) return null;

  return (
    <dl className="flex flex-wrap items-stretch gap-y-5">
      {figures.map(({ column, kind }, index) => {
        const cell = formatCell(row[column] ?? null, kind);
        return (
          <div
            key={column}
            className={
              index === 0
                ? "pe-8"
                : "border-s border-border ps-8 pe-8 last:pe-0"
            }
          >
            <dt className="label-xs mb-2 text-muted-foreground">
              {humanizeColumn(column)}
            </dt>
            <dd
              className="figure text-[clamp(1.75rem,1.3rem+1.6vw,2.5rem)] font-semibold leading-none text-foreground"
              title={cell.isNull ? "No value" : cell.raw}
            >
              {cell.display}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
