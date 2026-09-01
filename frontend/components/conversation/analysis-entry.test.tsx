// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AnalysisEntry } from "@/components/conversation/analysis-entry";
import { DEFAULT_DATA_SOURCE_ID } from "@/lib/datasources/datasources";
import type { AnalyticsResponse } from "@/lib/types/analytics";

afterEach(cleanup);

function clarifying(
  choices: AnalyticsResponse["clarification_choices"],
): AnalyticsResponse {
  return {
    schema_version: "1.1",
    request_id: "req-1",
    thread_id: "thread-1",
    data_source_id: DEFAULT_DATA_SOURCE_ID,
    model_profile: "gemini_pro",
    model_display_name: "Gemini",
    status: "clarification_required",
    answer: "Which Organizational Unit do you mean: OU2100 | Operations; OU2200 | Operations?",
    columns: [],
    rows: [],
    chart: null,
    sources: [],
    provenance: {
      source: "postgres:legacy",
      tables: [],
      columns: [],
      result: { row_count: 0, columns: [] },
      freshness: {},
    } as unknown as AnalyticsResponse["provenance"],
    freshness: {} as AnalyticsResponse["freshness"],
    clarification_required: true,
    clarification_question:
      "Which Organizational Unit do you mean: OU2100 | Operations; OU2200 | Operations?",
    clarification_choices: choices,
    data_quality: [],
    warnings: [],
    execution: {
      query_id: null,
      status: "clarification_required",
      row_count: 0,
      duration_ms: 0,
      executed_at: null,
    } as unknown as AnalyticsResponse["execution"],
  };
}

const OPERATIONS = [
  { value: "OU2100", label: "Operations (OU2100)" },
  { value: "OU2200", label: "Operations (OU2200)" },
];

describe("AnalysisEntry clarification", () => {
  it("offers each canonical option instead of asking the reader to retype one", async () => {
    const onAsk = vi.fn();
    render(
      <AnalysisEntry
        question="Show payroll for Operations."
        response={clarifying(OPERATIONS)}
        onOpenDetails={vi.fn()}
        onAsk={onAsk}
        disabled={false}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Operations (OU2200)" }),
    );

    // The identifier is what continues the analysis; the label is only shown.
    expect(onAsk).toHaveBeenCalledWith("OU2200");
  });

  it("does not repeat the options inside the sentence when it shows them", () => {
    render(
      <AnalysisEntry
        question="Show payroll for Operations."
        response={clarifying(OPERATIONS)}
        onOpenDetails={vi.fn()}
        onAsk={vi.fn()}
        disabled={false}
      />,
    );

    expect(screen.getByText("Which Organizational Unit do you mean?")).toBeTruthy();
  });

  it("names no table or column", () => {
    const { container } = render(
      <AnalysisEntry
        question="Show payroll for Operations."
        response={clarifying(OPERATIONS)}
        onOpenDetails={vi.fn()}
        onAsk={vi.fn()}
        disabled={false}
      />,
    );

    expect(container.textContent).not.toContain("org_unit_lkp");
    expect(container.textContent).not.toContain("org_cd");
  });

  it("falls back to the full question when there are no options to show", () => {
    render(
      <AnalysisEntry
        question="Show payroll for Operations."
        response={clarifying([])}
        onOpenDetails={vi.fn()}
        onAsk={vi.fn()}
        disabled={false}
      />,
    );

    expect(screen.queryByRole("button", { name: /OU2100/ })).toBeNull();
    expect(
      screen.getByText(/OU2100 \| Operations; OU2200 \| Operations/),
    ).toBeTruthy();
  });

  it("cannot be answered twice while a request is in flight", () => {
    render(
      <AnalysisEntry
        question="Show payroll for Operations."
        response={clarifying(OPERATIONS)}
        onOpenDetails={vi.fn()}
        onAsk={vi.fn()}
        disabled
      />,
    );

    const option = screen.getByRole("button", { name: "Operations (OU2100)" });
    expect(option.hasAttribute("disabled")).toBe(true);
  });
});
