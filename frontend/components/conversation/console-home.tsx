"use client";

import { motion } from "motion/react";
import { ArrowUpRight, ShieldCheck, Sparkle } from "lucide-react";

import { DUR, EASE_ENTRANCE } from "@/lib/motion";

/**
 * The console — the workspace before an analysis exists.
 *
 * This is the one screen allowed to be expressive. A single focal column:
 * wordmark, promise, composer (supplied by the parent), then real runnable
 * questions. The moment a question is asked the whole thing lifts away and the
 * ledger takes over.
 */

export interface SuggestedPrompt {
  label: string;
  question: string;
  kind: "governed" | "adhoc";
  dir?: "rtl";
}

export const SUGGESTED_PROMPTS: SuggestedPrompt[] = [
  {
    label: "Total annual payroll by department",
    question: "Total annual payroll by department",
    kind: "governed",
  },
  {
    label: "Active headcount by department",
    question: "Active headcount by department",
    kind: "governed",
  },
  {
    label: "Department headcount, total, average and top earner",
    question:
      "Show each department, its number of employees, total salary, average salary, and highest paid employee, ordered by total payroll.",
    kind: "adhoc",
  },
  {
    label: "إجمالي الرواتب السنوية حسب القسم",
    question: "إجمالي الرواتب السنوية حسب القسم",
    kind: "governed",
    dir: "rtl",
  },
];

const stagger = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.06, delayChildren: 0.05 } },
};

const rise = {
  hidden: { opacity: 0, y: 12 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.5, ease: EASE_ENTRANCE },
  },
};

export function ConsoleHome({
  onSelect,
  composer,
}: {
  onSelect: (question: string) => void;
  composer: React.ReactNode;
}) {
  return (
    <motion.div
      variants={stagger}
      initial="hidden"
      animate="visible"
      className="relative z-10 mx-auto flex w-full max-w-2xl flex-col items-center px-5 text-center"
    >
      <motion.p variants={rise} className="label-xs text-primary">
        Enterprise Data Agent
      </motion.p>

      <motion.h1
        variants={rise}
        className="mt-4 text-[clamp(2rem,1.4rem+2.4vw,3.25rem)] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground"
      >
        Ask anything
        <br />
        about your data
      </motion.h1>

      <motion.p
        variants={rise}
        className="mt-4 max-w-md text-[15px] leading-relaxed text-muted-foreground"
      >
        Every answer is grounded in executed query results and cites the tables
        it came from. Certified metrics use governed definitions — never
        improvised SQL.
      </motion.p>

      <motion.div variants={rise} className="mt-9 w-full">
        {composer}
      </motion.div>

      <motion.div variants={rise} className="mt-10 w-full">
        <div className="mb-3 flex items-center gap-2.5">
          <Sparkle className="size-3 text-muted-foreground" aria-hidden="true" />
          <span className="label-xs text-muted-foreground">Try one of these</span>
          <span className="rule-fade flex-1" aria-hidden="true" />
        </div>

        <ul className="grid gap-1.5 sm:grid-cols-2">
          {SUGGESTED_PROMPTS.map((prompt, index) => (
            <motion.li
              key={prompt.question}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{
                duration: DUR.slow,
                ease: EASE_ENTRANCE,
                delay: 0.35 + index * 0.05,
              }}
            >
              <button
                type="button"
                onClick={() => onSelect(prompt.question)}
                className="group relative flex h-full w-full items-start gap-2.5 overflow-hidden rounded-xl border border-border bg-surface/60 px-3.5 py-3 text-start transition-all hover:border-primary/40 hover:bg-surface hover:shadow-float active:scale-[0.99]"
              >
                <span className="min-w-0 flex-1">
                  <span
                    dir={prompt.dir ?? "auto"}
                    className="block text-[13px] font-medium leading-snug text-foreground"
                  >
                    {prompt.label}
                  </span>
                  <span className="mt-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    {prompt.kind === "governed" ? (
                      <>
                        <ShieldCheck
                          className="size-3 text-success"
                          aria-hidden="true"
                        />
                        Governed metric
                      </>
                    ) : (
                      <>
                        <span
                          aria-hidden="true"
                          className="size-1 rounded-full bg-muted-foreground"
                        />
                        Ad-hoc analysis
                      </>
                    )}
                  </span>
                </span>
                <ArrowUpRight
                  className="mt-0.5 size-3.5 shrink-0 text-muted-foreground transition-transform duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-primary"
                  aria-hidden="true"
                />
              </button>
            </motion.li>
          ))}
        </ul>
      </motion.div>
    </motion.div>
  );
}
