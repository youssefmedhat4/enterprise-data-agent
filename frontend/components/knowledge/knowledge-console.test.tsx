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
function mockApi(semantics: unknown[] = [], status = 200) {
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

beforeEach(() => vi.restoreAllMocks());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Schema review", () => {
  it("renders a proposal with its physical source and confidence", async () => {
    mockApi([ENTITY_PROPOSAL]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await userEvent.click(await screen.findByRole("tab", { name: /Schema review/ }));

    expect(await screen.findByText("Employee")).toBeTruthy();
    expect(screen.getByText("analytics.staff")).toBeTruthy();
    expect(screen.getByText("97%")).toBeTruthy();
  });

  it("approving sends the decision to the backend", async () => {
    const calls = mockApi([ENTITY_PROPOSAL]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await userEvent.click(await screen.findByRole("tab", { name: /Schema review/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));

    await waitFor(() => {
      const review = calls.find(
        (call) => call.method === "POST" && call.url.includes("/semantics/"),
      );
      expect(review).toBeTruthy();
      expect((review?.body as { action: string }).action).toBe("approve");
    });
  });

  it("an edited name is sent with the approval", async () => {
    const calls = mockApi([ENTITY_PROPOSAL]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await userEvent.click(await screen.findByRole("tab", { name: /Schema review/ }));
    await userEvent.type(
      await screen.findByLabelText(/Corrected meaning/),
      "Staff Member",
    );
    await userEvent.click(screen.getByRole("button", { name: /Save & approve/ }));

    await waitFor(() => {
      const review = calls.find(
        (call) => call.method === "POST" && call.url.includes("/semantics/"),
      );
      expect((review?.body as { concept_name: string }).concept_name).toBe(
        "Staff Member",
      );
    });
  });

  it("rejecting sends a reject decision", async () => {
    const calls = mockApi([ENTITY_PROPOSAL]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

    await userEvent.click(await screen.findByRole("tab", { name: /Schema review/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));

    await waitFor(() => {
      const review = calls.find(
        (call) => call.method === "POST" && call.url.includes("/semantics/"),
      );
      expect((review?.body as { action: string }).action).toBe("reject");
    });
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


describe("Data source registration", () => {
  it("offers only server-supplied connections, never a credential field", async () => {
    mockApi([]);
    render(<KnowledgeConsole dataSourceId={DEFAULT_DATA_SOURCE_ID} />);

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

    await userEvent.click(
      await screen.findByRole("button", { name: /Reindex semantic search/ }),
    );

    expect(await screen.findByRole("status")).toBeTruthy();
  });
});
