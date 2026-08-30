import { toNumber } from "@/lib/format/values";
import type { ChartSpec, ResultRow } from "@/lib/types/analytics";

/**
 * Alternative ways to present one already-validated result.
 *
 * The backend picked and validated the recommended chart. This module answers a
 * narrower question: given those same rows, which *other* renderers would also
 * be correct? The user can then switch presentation without another query,
 * another model call, or any change to the data.
 *
 * The rules below deliberately mirror `ChartValidator` on the backend. This is a
 * presentation filter layered on top of server validation, never a replacement
 * for it: every option is a transform of the server's own validated spec, so no
 * option can reference a column the backend did not already approve.
 */

/** Matches `MAX_PART_TO_WHOLE_SLICES` in app/agent/charts.py. */
const MAX_SLICES = 12;

const TEMPORAL_TYPES = new Set([
  "date",
  "timestamp",
  "timestamptz",
  "timestamp with time zone",
  "timestamp without time zone",
]);

export interface ChartPresentation {
  /** Stable identity for selection state. */
  id: string;
  label: string;
  /** A derived spec. The server's spec object is never mutated. */
  spec: ChartSpec;
  /** True for the backend's own recommendation. */
  recommended: boolean;
}

interface Shape {
  measuresNumeric: boolean;
  xNumeric: boolean;
  nonNegative: boolean;
  categoryCount: number;
  multiSeries: boolean;
  temporal: boolean;
}

function inspect(
  spec: ChartSpec,
  rows: ResultRow[],
  columnTypes: Record<string, string>,
): Shape {
  const measureValues = spec.measures.flatMap((measure) =>
    rows.map((row) => toNumber(row[measure] ?? null)),
  );
  const presentMeasures = measureValues.filter(
    (value): value is number => value !== null,
  );
  const xValues = rows.map((row) => toNumber(row[spec.x] ?? null));

  const declared = columnTypes[spec.x]?.toLowerCase();

  return {
    measuresNumeric:
      presentMeasures.length > 0 && presentMeasures.length === measureValues.length,
    xNumeric: rows.length > 0 && xValues.every((value) => value !== null),
    nonNegative: presentMeasures.every((value) => value >= 0),
    categoryCount: new Set(rows.map((row) => String(row[spec.x] ?? "—"))).size,
    multiSeries: spec.measures.length > 1 || spec.series !== null,
    // A declared temporal column is proof. Failing that, the model choosing a
    // line or area is its own judgement that the x axis is meaningfully ordered,
    // which is a signal column types alone cannot provide.
    temporal:
      (declared !== undefined && TEMPORAL_TYPES.has(declared)) ||
      spec.type === "line" ||
      spec.type === "area",
  };
}

function derive(spec: ChartSpec, patch: Partial<ChartSpec>): ChartSpec {
  return { ...spec, ...patch };
}

/**
 * Compatible presentations for this result, recommendation first.
 *
 * Returns a single entry when nothing else is safely renderable, in which case
 * the caller should hide the selector rather than offer a menu of one.
 */
export function chartPresentations(
  spec: ChartSpec,
  rows: ResultRow[],
  columnTypes: Record<string, string> = {},
): ChartPresentation[] {
  if (rows.length === 0) return [];

  const shape = inspect(spec, rows, columnTypes);
  const options: Omit<ChartPresentation, "recommended">[] = [];

  const add = (id: string, label: string, patch: Partial<ChartSpec>) => {
    options.push({ id, label, spec: derive(spec, patch) });
  };

  if (shape.measuresNumeric) {
    if (shape.multiSeries) {
      add("bar-grouped", "Grouped bar", {
        type: "bar",
        orientation: "vertical",
        mode: "grouped",
      });
      // Stacking negatives renders a bar that crosses its own baseline and
      // reads as a smaller total than the parts, so it is withheld.
      if (shape.nonNegative) {
        add("bar-stacked", "Stacked bar", {
          type: "bar",
          orientation: "vertical",
          mode: "stacked",
        });
      }
    } else {
      add("bar", "Bar", { type: "bar", orientation: "vertical", mode: "grouped" });
    }

    add("bar-horizontal", "Horizontal bar", {
      type: "bar",
      orientation: "horizontal",
      mode: shape.multiSeries && shape.nonNegative ? spec.mode : "grouped",
    });

    if (shape.temporal) {
      add("line", shape.multiSeries ? "Multi-series line" : "Line", {
        type: "line",
      });
      add("area", "Area", {
        type: "area",
        mode: shape.multiSeries && shape.nonNegative ? spec.mode : "grouped",
      });
    }

    // Part-to-whole needs one non-negative measure over few enough categories,
    // and a categorical axis — the same conditions the backend enforces.
    if (
      !shape.multiSeries &&
      shape.nonNegative &&
      !shape.xNumeric &&
      shape.categoryCount > 1 &&
      shape.categoryCount <= MAX_SLICES
    ) {
      add("pie", "Pie", { type: "pie", series: null });
      add("donut", "Donut", { type: "donut", series: null });
    }
  }

  if (shape.xNumeric && shape.measuresNumeric && spec.measures.length === 1) {
    add("scatter", "Scatter", { type: "scatter", series: null });
  }

  const recommendedId = identify(spec);
  const matched = options.find((option) => option.id === recommendedId);

  // The server spec always leads, and is rendered exactly as it arrived rather
  // than as a reconstruction of itself.
  const recommendation: ChartPresentation = {
    id: matched?.id ?? recommendedId,
    label: matched?.label ?? labelFor(spec),
    spec,
    recommended: true,
  };

  return [
    recommendation,
    ...options
      .filter((option) => option.id !== recommendation.id)
      .map((option) => ({ ...option, recommended: false })),
  ];
}

/** Stable id for a spec's visual presentation. */
export function identify(spec: ChartSpec): string {
  if (spec.type === "bar") {
    if (spec.orientation === "horizontal") return "bar-horizontal";
    if (spec.measures.length > 1 || spec.series !== null) {
      return spec.mode === "stacked" ? "bar-stacked" : "bar-grouped";
    }
    return "bar";
  }
  return spec.type;
}

function labelFor(spec: ChartSpec): string {
  const labels: Record<string, string> = {
    bar: "Bar",
    "bar-grouped": "Grouped bar",
    "bar-stacked": "Stacked bar",
    "bar-horizontal": "Horizontal bar",
    line: "Line",
    area: "Area",
    pie: "Pie",
    donut: "Donut",
    scatter: "Scatter",
  };
  return labels[identify(spec)] ?? "Chart";
}
