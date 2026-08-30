// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/conversation/composer";
import { ModelSelector } from "@/components/conversation/model-selector";

afterEach(cleanup);

describe("ModelSelector", () => {
  it("renders Qwen by default and offers both approved profiles", () => {
    render(
      createElement(ModelSelector, {
        value: "qwen",
        onValueChange: vi.fn(),
        disabled: false,
      }),
    );

    const trigger = screen.getByRole("button", { name: "Model: Qwen 3.6 27B" });
    expect(trigger.textContent).toContain("Qwen 3.6 27B");
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });

    expect(screen.getAllByText("Qwen 3.6 27B").length).toBeGreaterThan(0);
    expect(screen.getByText("Gemini 2.5 Flash")).toBeTruthy();
  });

  it("is disabled while a request is running", () => {
    render(
      createElement(Composer, {
        onSubmit: vi.fn(),
        onStop: vi.fn(),
        isBusy: true,
        modelProfile: "qwen",
        onModelProfileChange: vi.fn(),
      }),
    );

    expect(
      screen.getByRole("button", { name: "Model: Qwen 3.6 27B" }).hasAttribute("disabled"),
    ).toBe(true);
  });

  it("keeps the same selected profile across focal and docked composer layouts", () => {
    const props = {
      onSubmit: vi.fn(),
      onStop: vi.fn(),
      isBusy: false,
      modelProfile: "gemini" as const,
      onModelProfileChange: vi.fn(),
    };
    const { rerender } = render(createElement(Composer, { ...props, tone: "focal" }));
    expect(screen.getByRole("button", { name: "Model: Gemini 2.5 Flash" })).toBeTruthy();

    rerender(createElement(Composer, { ...props, tone: "docked" }));
    expect(screen.getByRole("button", { name: "Model: Gemini 2.5 Flash" })).toBeTruthy();
  });
});
