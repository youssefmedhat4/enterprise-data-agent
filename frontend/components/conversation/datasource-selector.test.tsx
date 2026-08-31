// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/conversation/composer";
import { DataSourceSelector } from "@/components/conversation/datasource-selector";
import {
  DEFAULT_DATA_SOURCE,
  DEFAULT_DATA_SOURCE_ID,
  type DataSourceSummary,
} from "@/lib/datasources/datasources";
import { DEFAULT_MODEL_PROFILE } from "@/lib/models/profiles";

const WAREHOUSE: DataSourceSummary = {
  ...DEFAULT_DATA_SOURCE,
  id: "11111111-2222-3333-4444-555555555555",
  name: "EU Warehouse",
  connectionRef: "WAREHOUSE_URL",
  isDefault: false,
};

afterEach(cleanup);

describe("DataSourceSelector", () => {
  it("shows which database is active", () => {
    render(
      <DataSourceSelector
        value={DEFAULT_DATA_SOURCE_ID}
        sources={[DEFAULT_DATA_SOURCE, WAREHOUSE]}
        onValueChange={vi.fn()}
        disabled={false}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Data source: Company Analytics" }),
    ).toBeTruthy();
  });

  it("reports the chosen datasource id", async () => {
    const onValueChange = vi.fn();
    render(
      <DataSourceSelector
        value={DEFAULT_DATA_SOURCE_ID}
        sources={[DEFAULT_DATA_SOURCE, WAREHOUSE]}
        onValueChange={onValueChange}
        disabled={false}
      />,
    );

    await userEvent.click(screen.getByRole("button"));
    await userEvent.click(screen.getByText("EU Warehouse"));

    expect(onValueChange).toHaveBeenCalledWith(WAREHOUSE.id);
  });

  it("is inert while a request is running", () => {
    render(
      <DataSourceSelector
        value={DEFAULT_DATA_SOURCE_ID}
        sources={[DEFAULT_DATA_SOURCE, WAREHOUSE]}
        onValueChange={vi.fn()}
        disabled
      />,
    );

    expect(screen.getByRole("button").hasAttribute("disabled")).toBe(true);
  });

  it("does not offer a choice when only one database is registered", () => {
    render(
      <DataSourceSelector
        value={DEFAULT_DATA_SOURCE_ID}
        sources={[DEFAULT_DATA_SOURCE]}
        onValueChange={vi.fn()}
        disabled={false}
      />,
    );

    expect(screen.getByRole("button").hasAttribute("disabled")).toBe(true);
  });

  it("renders no connection reference or secret", () => {
    const { container } = render(
      <DataSourceSelector
        value={WAREHOUSE.id}
        sources={[DEFAULT_DATA_SOURCE, WAREHOUSE]}
        onValueChange={vi.fn()}
        disabled={false}
      />,
    );

    expect(container.textContent).not.toContain("WAREHOUSE_URL");
    expect(container.textContent).not.toContain("://");
  });
});

describe("Composer", () => {
  it("surfaces the active datasource alongside the model", () => {
    render(
      <Composer
        onSubmit={vi.fn()}
        onStop={vi.fn()}
        isBusy={false}
        modelProfile={DEFAULT_MODEL_PROFILE}
        onModelProfileChange={vi.fn()}
        dataSourceId={DEFAULT_DATA_SOURCE_ID}
        dataSources={[DEFAULT_DATA_SOURCE, WAREHOUSE]}
        onDataSourceChange={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Data source: Company Analytics" }),
    ).toBeTruthy();
  });
});
