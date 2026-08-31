import { describe, expect, it } from "vitest";

import {
  canRetryHere,
  dataSourceName,
  DEFAULT_DATA_SOURCE,
  DEFAULT_DATA_SOURCE_ID,
  parseDataSources,
} from "./datasources";

const OTHER_ID = "11111111-2222-3333-4444-555555555555";

describe("parseDataSources", () => {
  it("keeps only the safe fields the admin API returns", () => {
    const [parsed] = parseDataSources([
      {
        id: DEFAULT_DATA_SOURCE_ID,
        name: "Company Analytics",
        database_type: "postgres",
        connection_ref: "DATABASE_URL",
        status: "READY",
        is_default: true,
        certified_metric_count: 7,
      },
    ]);

    expect(parsed.name).toBe("Company Analytics");
    expect(parsed.connectionRef).toBe("DATABASE_URL");
    expect(parsed.certifiedMetricCount).toBe(7);
  });

  it("drops entries with no id rather than inventing one", () => {
    expect(parseDataSources([{ name: "Nameless" }])).toEqual([]);
  });

  it("returns nothing for a malformed payload", () => {
    expect(parseDataSources(null)).toEqual([]);
    expect(parseDataSources({ nope: true })).toEqual([]);
  });

  it("never surfaces a value that looks like a credential", () => {
    // The backend does not send one; this guards the client from rendering it
    // if that ever changed.
    const [parsed] = parseDataSources([
      {
        id: OTHER_ID,
        name: "Warehouse",
        connection_ref: "WAREHOUSE_URL",
        password: "hunter2",
        dsn: "postgresql://user:secret@host/db",
      },
    ]);

    expect(JSON.stringify(parsed)).not.toContain("hunter2");
    expect(JSON.stringify(parsed)).not.toContain("://");
  });
});

describe("dataSourceName", () => {
  it("resolves a known id to its display name", () => {
    expect(dataSourceName([DEFAULT_DATA_SOURCE], DEFAULT_DATA_SOURCE_ID)).toBe(
      "Company Analytics",
    );
  });

  it("falls back rather than rendering a raw id", () => {
    expect(dataSourceName([], OTHER_ID)).toBe("Company Analytics");
  });
});

describe("canRetryHere", () => {
  it("allows a retry against the datasource that answered", () => {
    expect(canRetryHere(DEFAULT_DATA_SOURCE_ID, DEFAULT_DATA_SOURCE_ID)).toBe(true);
  });

  it("refuses a retry once the workspace switched database", () => {
    // Re-running the same wording against another database would silently
    // answer a different question.
    expect(canRetryHere(DEFAULT_DATA_SOURCE_ID, OTHER_ID)).toBe(false);
  });
});
