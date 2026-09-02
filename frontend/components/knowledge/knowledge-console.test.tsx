// @vitest-environment jsdom

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeConsole } from "@/components/knowledge/knowledge-console";
import { DEFAULT_DATA_SOURCE_ID } from "@/lib/datasources/datasources";

const ENTITY_PROPOSAL = {
  id: "11111111-1111-1111-1111-111111111111",
  kind: "entity",
  physical: "analytics.staff",
  proposed_concept: "Employee",
  confidence: 0.97,
  status: "PROPOSED",
  detail: "",
  entity_name: "Employee",
  schema_name: "analytics",
  table_name: "staff",
};

const ARABIC_NAME = {
  id: "44444444-4444-4444-4444-444444444444",
  kind: "attribute",
  physical: "analytics.staff.arabic_name",
  proposed_concept: "Arabic Name",
  confidence: 0.9,
  status: "PROPOSED",
  detail: "",
  entity_name: "Employee",
  schema_name: "analytics",
  table_name: "staff",
  column_name: "arabic_name",
  data_type: "text",
  is_identifier: false,
};

const STAFF_NUMBER = {
  ...ARABIC_NAME,
  id: "55555555-5555-5555-5555-555555555555",
  physical: "analytics.staff.staff_no",
  proposed_concept: "Employee ID",
  column_name: "staff_no",
  is_identifier: true,
};

const REPORTS_TO = {
  id: "66666666-6666-6666-6666-666666666666",
  kind: "relationship",
  physical: "staff.dept_id -> departments.dept_id",
  proposed_concept: "belongs to",
  confidence: 0.88,
  status: "PROPOSED",
  detail: "many_to_one",
  from_entity: "Employee",
  to_entity: "Department",
};

const STALE_MAPPING = {
  id: "22222222-2222-2222-2222-222222222222",
  kind: "attribute",
  physical: "analytics.staff.salary",
  proposed_concept: "Annual Base Salary",
  confidence: 0.9,
  status: "STALE",
  detail: "",
};

const CONFIRMED_MAPPING = {
  id: "33333333-3333-3333-3333-333333333333",
  kind: "entity",
  physical: "analytics.business_units",
  proposed_concept: "Organizational Unit",
  confidence: 0.95,
  status: "CONFIRMED",
  detail: "",
};

/** Records every request so tests can assert what the UI actually sent. */
function mockApi(
  semantics: unknown[] = [],
  status = 200,
  previews: unknown[] = [],
) {
  const calls: Array<{ url: string; method: string; body: unknown }> = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({
      url,
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    if (status !== 200) {
      return { ok: false, status, json: async () => ({ detail: "denied" }) };
    }
    if (url.includes("/connection-refs")) {
      return { ok: true, status: 200, json: async () => ["DATABASE_URL"] };
    }
    if (url.includes("/column-previews")) {
      return { ok: true, status: 200, json: async () => previews };
    }
    if (url.includes("/semantics") && (init?.method ?? "GET") === "GET") {
      return { ok: true, status: 200, json: async () => semantics };
    }
    if (init?.method === "POST") {
      return { ok: true, status: 200, json: async () => ({}) };
    }
    return { ok: true, status: 200, json: async () => [] };
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

/**
 * The console opens on Overview, so anything about a data source starts by
 * navigating there — the same click a reviewer makes.
 */
async function openDataSources() {
  await userEvent.click(await screen.findByRole("tab", { name: "Data sources" }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/knowledge");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/**
 * Schema review is where a person who does not read database notation has to
 * decide what a column means. These pin the parts of the screen that make that
 * possible: the concept leads, the physical path is still there but demoted,
 * and the sample values are labelled as examples rather than as the set of
 * values a column can hold.
 */
describe("Schema review", () => {
  async function openReview() {
    await userEvent.click(await screen.findByRole("tab", { name: /Schema review/ }));
  }

  it("leads with the concept and keeps the column path available", async () => {
    mockApi([ARABIC_NAME]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openReview();

    expect(await screen.findByRole("heading", { name: "Arabic Name" })).toBeTruthy();
    // The kind is explicit, while the open Employee group supplies its owner.
    expect(screen.getByText("ATTRIBUTE")).toBeTruthy();
    // Demoted, never removed.
    expect(screen.getByText("analytics.staff.arabic_name")).toBeTruthy();
  });

  it("groups attributes under the concept they belong to", async () => {
    mockApi([ENTITY_PROPOSAL, ARABIC_NAME, STAFF_NUMBER]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openReview();

    const group = await screen.findByRole("button", {
      name: "Employee, 3 proposals",
    });
    expect(group.getAttribute("aria-expanded")).toBe("true");
    expect(await screen.findByRole("heading", { name: "Arabic Name" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Employee ID" })).toBeTruthy();
  });

  it("marks the canonical key and explains what one is", async () => {
    mockApi([STAFF_NUMBER]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openReview();

    expect(await screen.findByText("Canonical key")).toBeTruthy();
    expect(screen.getByLabelText("What a canonical key is")).toBeTruthy();
  });

  it("reads a relationship as two concepts rather than as a join", async () => {
    mockApi([REPORTS_TO]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openReview();

    const heading = await screen.findByRole("heading", {
      name: /Employee belongs to Department/,
    });
    expect(heading).toBeTruthy();
    expect(screen.getByText("staff.dept_id -> departments.dept_id")).toBeTruthy();
  });

  it("shows a bounded sample, labelled as examples", async () => {
    mockApi(
      [ARABIC_NAME],
      200,
      [{ column: "analytics.staff.arabic_name", values: ["\u0623\u062d\u0645\u062f", "\u0633\u0627\u0631\u0629"] }],
    );
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openReview();

    expect(await screen.findByText("\u0623\u062d\u0645\u062f")).toBeTruthy();
    expect(screen.getByText("Example values")).toBeTruthy();
    // Never described as the values the column can hold.
    expect(screen.queryByText(/possible values/i)).toBeNull();
    expect(screen.queryByText(/all values/i)).toBeNull();
  });

  it("says so when no safe sample is available", async () => {
    mockApi([ARABIC_NAME]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openReview();

    expect(await screen.findByText("No preview available")).toBeTruthy();
  });

  it("approving sends the decision to the backend", async () => {
    const calls = mockApi([ARABIC_NAME]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openReview();
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));

    await waitFor(() => {
      const review = calls.find(
        (call) => call.method === "POST" && call.url.includes("/semantics/"),
      );
      expect(review).toBeTruthy();
      expect((review?.body as { action: string }).action).toBe("approve");
    });
  });

  it("a renamed meaning is sent with the approval", async () => {
    const calls = mockApi([ARABIC_NAME]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openReview();
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.type(
      await screen.findByLabelText(/Business meaning/),
      "Name in Arabic",
    );
    await userEvent.click(screen.getByRole("button", { name: /Save & approve/ }));

    await waitFor(() => {
      const review = calls.find(
        (call) => call.method === "POST" && call.url.includes("/semantics/"),
      );
      expect((review?.body as { concept_name: string }).concept_name).toBe(
        "Name in Arabic",
      );
    });
  });

  it("rejecting sends a reject decision", async () => {
    const calls = mockApi([ARABIC_NAME]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openReview();
    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));

    await waitFor(() => {
      const review = calls.find(
        (call) => call.method === "POST" && call.url.includes("/semantics/"),
      );
      expect((review?.body as { action: string }).action).toBe("reject");
    });
  });

  it("approves a selected set one proposal at a time", async () => {
    const calls = mockApi([ARABIC_NAME, STAFF_NUMBER]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openReview();
    await userEvent.click(
      await screen.findByLabelText("Select Arabic Name for bulk approval"),
    );
    await userEvent.click(
      screen.getByLabelText("Select Employee ID for bulk approval"),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /Approve selected/ }),
    );

    await waitFor(() => {
      const reviews = calls.filter(
        (call) => call.method === "POST" && call.url.includes("/semantics/"),
      );
      // One request per proposal: bulk selection is a convenience, never a
      // different kind of decision.
      expect(reviews).toHaveLength(2);
      expect(
        reviews.every(
          (call) => (call.body as { action: string }).action === "approve",
        ),
      ).toBe(true);
    });
  });

  it("offers no way to approve everything at once", async () => {
    mockApi([ARABIC_NAME, STAFF_NUMBER]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openReview();
    await screen.findByRole("heading", { name: "Arabic Name" });

    expect(screen.queryByRole("button", { name: /approve all/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /select all/i })).toBeNull();
  });
});

describe("Confirmed semantics", () => {
  it("shows confirmed mappings and marks stale ones", async () => {
    mockApi([CONFIRMED_MAPPING, STALE_MAPPING]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await userEvent.click(
      await screen.findByRole("tab", { name: "Confirmed semantics" }),
    );

    expect(await screen.findByText("Organizational Unit")).toBeTruthy();
    expect(screen.getByText("STALE")).toBeTruthy();
    expect(screen.getByText(/no longer used/)).toBeTruthy();
  });

  it("keeps proposals out of the confirmed list", async () => {
    mockApi([ENTITY_PROPOSAL, CONFIRMED_MAPPING]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await userEvent.click(
      await screen.findByRole("tab", { name: "Confirmed semantics" }),
    );

    await screen.findByText("Organizational Unit");
    expect(screen.queryByText("Employee")).toBeNull();
  });
});

describe("Data sources", () => {
  it("offers a scan action and calls the scan endpoint", async () => {
    const calls = mockApi([]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openDataSources();
    await userEvent.click(await screen.findByRole("button", { name: /Scan/ }));

    await waitFor(() => {
      expect(calls.some((call) => call.url.endsWith("/scan"))).toBe(true);
    });
  });

  it("renders no credential for a data source", async () => {
    mockApi([]);
    const { container } = render(
      <KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />,
    );

    await screen.findByText("Knowledge");
    await openDataSources();
    expect(container.textContent).not.toContain("://");
    expect(container.textContent).not.toContain("password");
  });
});

describe("Authorization", () => {
  it("explains that review authority is required on 403", async () => {
    mockApi([], 403);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    expect(await screen.findByText(/Review authority required/)).toBeTruthy();
  });
});

describe("Learning provenance navigation", () => {
  const clusterId = "77777777-7777-7777-7777-777777777777";
  const candidateId = "88888888-8888-8888-8888-888888888888";
  const exampleId = "99999999-9999-9999-9999-999999999999";

  function mockLearningLifecycle() {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/connection-refs")) {
          return { ok: true, status: 200, json: async () => ["DATABASE_URL"] };
        }
        if (url.endsWith("/clusters")) {
          return {
            ok: true,
            status: 200,
            json: async () => [
              {
                id: clusterId,
                canonical_summary: "current and previous compensation",
                structural_fingerprint: "compensation-history",
                occurrence_count: 2,
                successful_count: 2,
                first_seen_at: "2026-01-01T00:00:00Z",
                last_seen_at: "2026-01-02T00:00:00Z",
                status: "ACTIVE",
                candidate_id: candidateId,
                candidate_status: "APPROVED",
                promoted_to_type: "QUERY_EXAMPLE",
                promoted_to_id: exampleId,
              },
            ],
          };
        }
        if (url.endsWith("/candidates")) {
          return {
            ok: true,
            status: 200,
            json: async () => [
              {
                id: candidateId,
                candidate_type: "QUERY_EXAMPLE",
                display_name: "Current and previous compensation",
                description: "A recurring comparison.",
                status: "APPROVED",
                evidence_count: 2,
                successful_evidence_count: 2,
                expression: null,
                grain: null,
                dependencies: [],
                rejection_reason: null,
                cluster_id: clusterId,
                promoted_to_type: "QUERY_EXAMPLE",
                promoted_to_id: exampleId,
                detail: [],
              },
            ],
          };
        }
        if (url.endsWith("/examples")) {
          return {
            ok: true,
            status: 200,
            json: async () => [
              {
                id: exampleId,
                question: "Current and previous compensation",
                semantic_plan: "Compare current compensation to its prior value.",
                status: "CONFIRMED",
                schema_fingerprint: "fp-1",
                approved_at: "2026-01-03T00:00:00Z",
                source_candidate_id: candidateId,
                source_cluster_id: clusterId,
                approved_by: "reviewer",
              },
            ],
          };
        }
        return { ok: true, status: 200, json: async () => [] };
      }),
    );
  }

  it("moves from a recurring question to its approved candidate", async () => {
    mockLearningLifecycle();
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await userEvent.click(
      await screen.findByRole("tab", { name: "Recurring questions" }),
    );
    expect(await screen.findByText("Candidate approved")).toBeTruthy();
    expect(screen.getByText("Approved example")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /View candidate/ }));

    expect(screen.getByRole("tab", { name: "Candidates" }).getAttribute("data-state"))
      .toBe("active");
    expect(await screen.findByText("Promoted to")).toBeTruthy();
  });

  it("moves from an approved candidate to its normalized store", async () => {
    mockLearningLifecycle();
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await userEvent.click(await screen.findByRole("tab", { name: "Candidates" }));
    await userEvent.click(
      await screen.findByRole("button", { name: /View promoted knowledge/ }),
    );

    expect(
      screen.getByRole("tab", { name: "Approved examples" }).getAttribute("data-state"),
    ).toBe("active");
    expect(await screen.findByText(/Compare current compensation/)).toBeTruthy();
  });
});


describe("Data source registration", () => {
  it("offers only server-supplied connections, never a credential field", async () => {
    mockApi([]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openDataSources();
    await userEvent.click(
      await screen.findByRole("button", { name: /Add data source/ }),
    );

    const connection = await screen.findByLabelText("Connection");
    expect(connection.tagName).toBe("SELECT");
    // A free-text box here would let someone paste a DSN.
    expect(screen.queryByLabelText(/password/i)).toBeNull();
    expect(screen.queryByLabelText(/dsn/i)).toBeNull();
    expect(
      within(connection as HTMLSelectElement).getByRole("option", {
        name: "DATABASE_URL",
      }),
    ).toBeTruthy();
  });

  it("registers with the chosen reference and no credential", async () => {
    const calls = mockApi([]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openDataSources();
    await userEvent.click(
      await screen.findByRole("button", { name: /Add data source/ }),
    );
    await userEvent.type(await screen.findByLabelText("Name"), "EU Warehouse");
    await userEvent.click(screen.getByRole("button", { name: "Register" }));

    await waitFor(() => {
      const post = calls.find(
        (call) => call.method === "POST" && call.url.endsWith("/data-sources"),
      );
      expect(post).toBeTruthy();
      const body = post?.body as Record<string, unknown>;
      expect(body.name).toBe("EU Warehouse");
      expect(body.connection_ref).toBe("DATABASE_URL");
      expect(JSON.stringify(body)).not.toContain("://");
      expect(Object.keys(body)).not.toContain("password");
    });
  });
});

describe("Semantic reindex", () => {
  it("calls the reindex endpoint for the data source", async () => {
    const calls = mockApi([]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openDataSources();
    await userEvent.click(
      await screen.findByRole("button", { name: /Reindex semantic search/ }),
    );

    await waitFor(() => {
      expect(calls.some((call) => call.url.endsWith("/reindex"))).toBe(true);
    });
  });

  it("reports the outcome to the reviewer", async () => {
    mockApi([]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await openDataSources();
    await userEvent.click(
      await screen.findByRole("button", { name: /Reindex semantic search/ }),
    );

    expect(await screen.findByRole("status")).toBeTruthy();
  });
});
