import { describe, expect, it } from "vitest";

import {
  formatMeasure,
  formatShare,
  formatSliceLabel,
  shareOfTotal,
} from "@/lib/format/values";

describe("measure formatting", () => {
  it("never turns a raw amount into a percentage", () => {
    // The original bug: value_format=percent was appended to a currency amount,
    // rendering a 710,000 payroll as "710,000%".
    expect(formatMeasure(710000, "currency")).not.toContain("%");
    expect(formatMeasure(710000, "number")).not.toContain("%");
  });

  it("formats currency with fixed decimals and no invented symbol", () => {
    const formatted = formatMeasure(710000, "currency");

    expect(formatted).toContain("710");
    expect(formatted).toMatch(/[.,]00$/);
    // The result carries no currency code, so no symbol may be asserted.
    expect(formatted).not.toMatch(/[$€£]/);
  });

  it("still formats genuinely percent-valued fields as percentages", () => {
    // Fractional measures carry two decimals, matching every other numeric
    // surface in the product.
    expect(formatMeasure(45.4, "percent")).toBe("45.40%");
    expect(formatMeasure(50, "percent")).toBe("50%");
  });
});

describe("share of total", () => {
  it("computes a slice share from the plotted values", () => {
    const share = shareOfTotal(710000, 1565000);

    expect(share).not.toBeNull();
    expect(share as number).toBeCloseTo(45.37, 1);
  });

  it.each([
    ["zero total", 0],
    ["negative total", -100],
    ["non-finite total", Number.NaN],
  ])("returns null for a %s rather than dividing", (_label, total) => {
    expect(shareOfTotal(100, total)).toBeNull();
  });

  it("renders a share with one decimal", () => {
    expect(formatShare(45.37)).toBe("45.4%");
  });
});

describe("part-to-whole slice labels", () => {
  const total = 1565000;

  it("shows amount and real share by default", () => {
    const label = formatSliceLabel(710000, total, "currency", "value_and_percent");

    expect(label).toContain("710,000.00");
    expect(label).toContain("45.4%");
    expect(label).not.toContain("710,000%");
  });

  it("shows only the share when asked", () => {
    expect(formatSliceLabel(710000, total, "currency", "percent")).toBe("45.4%");
  });

  it("shows only the value when asked", () => {
    const label = formatSliceLabel(710000, total, "currency", "value");

    expect(label).not.toContain("%");
  });

  it("falls back to the value when the total is zero", () => {
    const label = formatSliceLabel(0, 0, "currency", "value_and_percent");

    expect(label).not.toContain("%");
    expect(label).toContain("0");
  });
});
