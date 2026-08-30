"use client";

import { ArrowUp, Square } from "lucide-react";
import { motion } from "motion/react";
import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";

import { cn } from "@/lib/utils";
import { DUR, EASE_OUT } from "@/lib/motion";
import { ModelSelector } from "@/components/conversation/model-selector";
import type { ModelProfile } from "@/lib/models/profiles";

/**
 * The query composer.
 *
 * On the console it is the focal object and carries a soft accent halo; docked
 * beneath a running analysis it becomes quieter so the data holds attention.
 * Enter submits, Shift+Enter newlines, Escape stops an in-flight analysis.
 * `dir="auto"` lets an Arabic question align right as it is typed without
 * flipping the surrounding workspace.
 */

const MAX_HEIGHT_PX = 200;

export interface ComposerHandle {
  focus: () => void;
  setValue: (value: string) => void;
}

interface ComposerProps {
  onSubmit: (question: string) => void;
  onStop: () => void;
  isBusy: boolean;
  disabled?: boolean;
  placeholder?: string;
  handleRef?: RefObject<ComposerHandle | null>;
  /** `focal` on the console, `docked` beneath an active analysis. */
  tone?: "focal" | "docked";
  modelProfile: ModelProfile;
  onModelProfileChange: (profile: ModelProfile) => void;
}

export function Composer({
  onSubmit,
  onStop,
  isBusy,
  disabled = false,
  placeholder = "Ask a business question…",
  handleRef,
  tone = "docked",
  modelProfile,
  onModelProfileChange,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const resize = useCallback(() => {
    const element = textareaRef.current;
    if (element === null) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, []);

  useEffect(resize, [value, resize]);

  useImperativeHandle(
    handleRef,
    () => ({
      focus: () => textareaRef.current?.focus(),
      setValue: (next: string) => {
        setValue(next);
        requestAnimationFrame(() => textareaRef.current?.focus());
      },
    }),
    [],
  );

  const canSubmit = value.trim() !== "" && !isBusy && !disabled;
  const focal = tone === "focal";

  const submit = () => {
    if (!canSubmit) return;
    onSubmit(value.trim());
    setValue("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
      return;
    }
    if (event.key === "Escape" && isBusy) {
      event.preventDefault();
      onStop();
    }
  };

  return (
    <div className="relative">
      {/* Halo. Decorative, focal state only, and it never intercepts pointers. */}
      {focal ? (
        <motion.div
          aria-hidden="true"
          animate={{ opacity: focused ? 1 : 0.45, scale: focused ? 1.015 : 1 }}
          transition={{ duration: DUR.base, ease: EASE_OUT }}
          className="pointer-events-none absolute -inset-3 rounded-[1.5rem] blur-xl"
          style={{
            background:
              "radial-gradient(60% 100% at 50% 50%, oklch(from var(--primary) l c h / 0.24), transparent 70%)",
          }}
        />
      ) : null}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        className={cn(
          "relative flex items-end gap-2 border bg-surface transition-all duration-200",
          focal
            ? "rounded-2xl px-4 py-3.5 shadow-float"
            : "rounded-xl px-3.5 py-2.5 shadow-raise",
          focused
            ? "border-primary ring-[3px] ring-primary/20"
            : "border-input hover:border-border-strong",
          disabled ? "opacity-60" : "",
        )}
      >
        <label htmlFor="composer-input" className="sr-only">
          Ask a question about your data
        </label>
        <textarea
          id="composer-input"
          ref={textareaRef}
          dir="auto"
          rows={1}
          value={value}
          disabled={disabled}
          placeholder={placeholder}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          className={cn(
            // The wrapping form already draws the focus ring, so the textarea
            // opts out of the global outline rather than doubling it.
            "max-h-[200px] min-h-6 flex-1 resize-none border-0 bg-transparent p-0 text-foreground placeholder:text-muted-foreground focus-visible:outline-none disabled:cursor-not-allowed",
            focal ? "text-[16px] leading-relaxed" : "text-[15px] leading-relaxed",
          )}
        />

        {isBusy ? (
          <button
            type="button"
            onClick={onStop}
            className="grid size-8 shrink-0 place-items-center rounded-lg border border-border bg-surface-raised text-foreground transition-all hover:border-border-strong active:scale-95"
          >
            <Square className="size-3 fill-current" aria-hidden="true" />
            <span className="sr-only">Stop the running analysis</span>
          </button>
        ) : (
          <button
            type="submit"
            disabled={!canSubmit}
            className={cn(
              "grid size-8 shrink-0 place-items-center rounded-lg transition-all duration-150",
              canSubmit
                ? "bg-primary text-primary-foreground hover:brightness-110 active:scale-95"
                : "bg-muted text-muted-foreground",
            )}
          >
            <ArrowUp className="size-4" aria-hidden="true" />
            <span className="sr-only">Send question</span>
          </button>
        )}
      </form>

      <div
        className={cn(
          "mt-2 flex min-w-0 items-center gap-2 text-[11px] text-muted-foreground",
          focal ? "justify-between px-1" : "px-1",
        )}
      >
        <ModelSelector
          value={modelProfile}
          onValueChange={onModelProfileChange}
          disabled={isBusy}
          compact={!focal}
        />
        <p className="hidden min-w-0 text-end sm:block">
          <kbd className="font-sans font-medium text-foreground">Enter</kbd> to send
          {isBusy ? (
            <>
              {" "}
              · <kbd className="font-sans font-medium text-foreground">Esc</kbd> to stop
            </>
          ) : null}
        </p>
      </div>
    </div>
  );
}
