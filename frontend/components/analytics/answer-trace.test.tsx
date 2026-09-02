// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";

import { AnswerTrace } from "@/components/analytics/answer-trace";
import { DEFAULT_DATA_SOURCE_ID } from "@/lib/datasources/datasources";
import type { AnalyticsResponse } from "@/lib/types/analytics";

afterEach(cleanup);

it("shows the authoritative knowledge used and links its reviewed origin", async () => {
  const candidateId = "88888888-8888-8888-8888-888888888888";
  const clusterId = "77777777-7777-7777-7777-777777777777";
  const exampleId = "99999999-9999-9999-9999-999999999999";
  const response = {
    rows: [],
    columns: [],
    answer: "Compensation history returned.",
    request_id: "request-1",
    thread_id: "thread-1",
    model_profile: "gemini",
    model_display_name: "Gemini 2.5 Flash",
    status: "completed",
    data_source_id: DEFAULT_DATA_SOURCE_ID,
    trace: {
      data_source: "Legacy ERP",
      route: "adhoc_analytics",
      execution_source: "database",
      semantic_entities: [],
      metrics: [],
      business_instructions: [],
      query_examples: [exampleId],
      knowledge_used: [
        {
          kind: "QUERY_EXAMPLE",
          id: exampleId,
          name: "Current and previous compensation",
          summary: "Used as an example for planning; fresh SQL was generated.",
          usage: "PLANNING_CONTEXT",
          destination_type: "QUERY_EXAMPLE",
          origin: {
            type: "LEARNED",
            candidate_id: candidateId,
            cluster_id: clusterId,
            candidate_name: "Current and previous compensation",
            candidate_status: "APPROVED",
            evidence_count: 2,
            successful_evidence_count: 2,
            review_decision: "APPROVED",
            approved_by: "reviewer",
            approved_at: "2026-01-03T00:00:00Z",
          },
        },
      ],
      resolved_entities: [],
      tables: [],
      metric_lineage: [],
      column_level: false,
      lineage_note: "",
      validation_status: "valid",
      grounded: true,
      data_quality: [],
      model_profile: "Gemini 2.5 Flash",
      total_latency_ms: 1,
      time: null,
      generated_sql: "SELECT employee_id FROM erp.compensation",
    },
  } as unknown as AnalyticsResponse;

  render(<AnswerTrace question="Show compensation history" response={response} />);
  await userEvent.click(screen.getByRole("button", { name: /Why this answer/ }));

  expect(screen.getByText("Learned / approved knowledge used")).toBeTruthy();
  expect(screen.getAllByText(/Used as an example for planning/)).toHaveLength(2);
  expect(screen.getByText(/Observed 2 times/)).toBeTruthy();
  expect(screen.getByRole("link", { name: "View candidate" }).getAttribute("href"))
    .toContain(`candidate=${candidateId}`);
  expect(
    screen.getByRole("link", { name: "View recurring question" }).getAttribute("href"),
  ).toContain(`cluster=${clusterId}`);
  expect(
    screen.getByRole("link", { name: "View promoted knowledge" }).getAttribute("href"),
  ).toContain(`knowledge=${exampleId}`);
});
