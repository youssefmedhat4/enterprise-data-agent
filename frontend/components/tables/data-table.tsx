"use client";

import { ArrowDown, ArrowUp, Check, Copy, Download } from "lucide-react";
import { useMemo, useState } from "react";

import { cn } from "@/lib/utils";
import {
  classifyColumn,
  formatCell,
  humanizeColumn,
  toNumber,
  type CellKind,
} from "@/lib/format/values";
import type { ResultRow } from "@/lib/types/analytics";

/**
 * Analytical result table.
 *
 * Renders exactly what the backend returned — no client-side aggregation or
 * derived metrics. Sorting only reorders rows that already exist. Numbers are
 * right-aligned with tabular figures so they compare vertically, and every cell
 * keeps its exact backend value in `title` and in the copy/CSV output.
 */

interface DataTableProps {
  columns: string[];
  rows: ResultRow[];
  columnTypes: Record<string, string>;
  caption?: string;
}

type SortState = { column: string; direction: "asc" | "desc" } | null;

/** Compact glyph for the column's physical type. */
const KIND_GLYPH: Record<CellKind, string> = {
  number: "#",
  temporal: "◷",
  boolean: "◑",
  text: "A",
  empty: "·",
};

export function DataTable({
  columns,
  rows,
  columnTypes,
  caption,
}: DataTableProps) {
  const [sort, setSort] = useState<SortState>(null);
  const [copied, setCopied] = useState(false);

  const kinds = useMemo(() => {
    const map = new Map<string, CellKind>();
    for (const column of columns) {
      map.set(column, classifyColumn(column, columnTypes, rows));
    }
    return map;
  }, [columns, columnTypes, rows]);

  const sortedRows = useMemo(() => {
    if (sort === null) return rows;
    const kind = kinds.get(sort.column) ?? "text";
    const factor = sort.direction === "asc" ? 1 : -1;

    return [...rows].sort((left, right) => {
      const a = left[sort.column];
      const b = right[sort.column];

      // Nulls sort last in both directions — they carry no rank.
      const aNull = a === null || a === undefined;
      const bNull = b === null || b === undefined;
      if (aNull && bNull) return 0;
      if (aNull) return 1;
      if (bNull) return -1;

      if (kind === "number") {
        const na = toNumber(a);
        const nb = toNumber(b);
        if (na !== null && nb !== null) return (na - nb) * factor;
      }
      if (kind === "temporal") {
        const ta = Date.parse(String(a));
        const tb = Date.parse(String(b));
        if (!Number.isNaN(ta) && !Number.isNaN(tb)) return (ta - tb) * factor;
      }
      return (
        String(a).localeCompare(String(b), undefined, { numeric: true }) * factor
      );
    });
  }, [rows, sort, kinds]);

  const toggleSort = (column: string) => {
    setSort((current) => {
      if (current === null || current.column !== column) {
        return { column, direction: "desc" };
      }
      if (current.direction === "desc") return { column, direction: "asc" };
      return null;
    });
  };

  const toDelimited = (separator: string) => {
    const escape = (value: string) =>
      separator === "," && /[",\n]/.test(value)
        ? `"${value.replace(/"/g, '""')}"`
        : value;
    const header = columns.map(escape).join(separator);
    const body = sortedRows.map((row) =>
      columns
        .map((column) => {
          const value = row[column];
          return escape(value === null || value === undefined ? "" : String(value));
        })
        .join(separator),
    );
    return [header, ...body].join("\n");
  };

  const copyTable = async () => {
    try {
      await navigator.clipboard.writeText(toDelimited("\t"));
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard blocked (insecure context or permission). Export still works.
    }
  };

  const downloadCsv = () => {
    // BOM so Excel reads Arabic and other non-ASCII text correctly.
    const blob = new Blob(["﻿", toDelimited(",")], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "analysis-result.csv";
    link.click();
    URL.revokeObjectURL(url);
  };

  if (columns.length === 0) return null;

  return (
    <div className="min-w-0">
      {/* Scroll container owns the sticky header context. */}
      <div className="max-h-[28rem] overflow-auto">
        <table className="w-full border-collapse text-[13px]">
          {caption !== undefined ? (
            <caption className="sr-only">{caption}</caption>
          ) : null}
          <thead className="sticky top-0 z-10">
            <tr>
              {columns.map((column) => {
                const kind = kinds.get(column) ?? "text";
                const numeric = kind === "number";
                const isSorted = sort?.column === column;
                return (
                  <th
                    key={column}
                    scope="col"
                    aria-sort={
                      isSorted
                        ? sort.direction === "asc"
                          ? "ascending"
                          : "descending"
                        : "none"
                    }
                    className="border-b border-border bg-surface-raised p-0 font-normal"
                  >
                    <button
                      type="button"
                      onClick={() => toggleSort(column)}
                      title={`Sort by ${humanizeColumn(column)}`}
                      className={cn(
                        "group/th flex w-full items-center gap-1.5 px-3.5 py-2.5 transition-colors hover:bg-muted/60",
                        numeric ? "justify-end" : "justify-start",
                      )}
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          "font-mono text-[10px] leading-none text-muted-foreground transition-opacity",
                          numeric ? "order-2" : "order-none",
                        )}
                      >
                        {KIND_GLYPH[kind]}
                      </span>
                      <span
                        className={cn(
                          "label-xs truncate transition-colors",
                          isSorted ? "text-foreground" : "text-muted-foreground",
                        )}
                      >
                        {humanizeColumn(column)}
                      </span>
                      {isSorted ? (
                        sort.direction === "asc" ? (
                          <ArrowUp
                            className="size-3 shrink-0 text-primary"
                            aria-hidden="true"
                          />
                        ) : (
                          <ArrowDown
                            className="size-3 shrink-0 text-primary"
                            aria-hidden="true"
                          />
                        )
                      ) : (
                        <span
                          aria-hidden="true"
                          className="size-3 shrink-0 opacity-0"
                        />
                      )}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((row, index) => (
              <tr key={index} className="group/row transition-colors hover:bg-muted/40">
                {columns.map((column, columnIndex) => {
                  const kind = kinds.get(column) ?? "text";
                  const cell = formatCell(row[column] ?? null, kind);
                  const numeric = kind === "number";
                  return (
                    <td
                      key={column}
                      dir={numeric ? "ltr" : "auto"}
                      title={cell.isNull ? "No value" : cell.raw}
                      className={cn(
                        "relative border-b border-hairline px-3.5 py-2.5 align-top",
                        numeric
                          ? "tnum whitespace-nowrap text-end font-medium"
                          : "max-w-[26rem] text-start wrap-anywhere",
                        cell.isNull ? "text-muted-foreground" : "text-foreground",
                      )}
                    >
                      {/* Accent edge marks the hovered row without a fill. */}
                      {columnIndex === 0 ? (
                        <span
                          aria-hidden="true"
                          className="absolute inset-y-0 start-0 w-[2px] scale-y-0 bg-primary opacity-0 transition-all duration-150 group-hover/row:scale-y-100 group-hover/row:opacity-100"
                        />
                      ) : null}
                      {cell.display}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Footer actions sit below the scroll area so they never scroll away. */}
      <div className="flex items-center justify-end gap-1 border-t border-border bg-surface-raised/40 px-2 py-1.5">
        <button
          type="button"
          onClick={copyTable}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          {copied ? (
            <Check className="size-3 text-success" aria-hidden="true" />
          ) : (
            <Copy className="size-3" aria-hidden="true" />
          )}
          {copied ? "Copied" : "Copy"}
        </button>
        <button
          type="button"
          onClick={downloadCsv}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Download className="size-3" aria-hidden="true" />
          CSV
        </button>
      </div>
    </div>
  );
}
