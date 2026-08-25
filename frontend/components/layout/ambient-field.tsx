"use client";

import { AnimatePresence, motion } from "motion/react";

import { DUR, EASE_OUT } from "@/lib/motion";

/**
 * The console's ambient field.
 *
 * Three slow radial washes over a faint engineering grid. It exists only on the
 * empty state — the moment an analysis starts it fades away so nothing competes
 * with the data. Purely decorative, so it is hidden from assistive technology,
 * and it animates only `transform`/`opacity`, which keeps it off the main thread.
 */
export function AmbientField({ visible }: { visible: boolean }) {
  return (
    <AnimatePresence>
      {visible ? (
        <motion.div
          aria-hidden="true"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: DUR.slow, ease: EASE_OUT }}
          className="pointer-events-none absolute inset-0 overflow-hidden"
        >
          <div className="grid-field absolute inset-0" />

          <div
            className="animate-ambient absolute left-1/2 top-[38%] h-[46rem] w-[46rem] -translate-x-1/2 -translate-y-1/2 rounded-full blur-3xl"
            style={{
              background:
                "radial-gradient(circle, var(--ambient-a) 0%, transparent 68%)",
            }}
          />
          <div
            className="animate-ambient-alt absolute left-[22%] top-[62%] h-[32rem] w-[32rem] rounded-full blur-3xl"
            style={{
              background:
                "radial-gradient(circle, var(--ambient-b) 0%, transparent 70%)",
            }}
          />
          <div
            className="animate-ambient absolute right-[16%] top-[24%] h-[28rem] w-[28rem] rounded-full blur-3xl"
            style={{
              animationDelay: "-8s",
              background:
                "radial-gradient(circle, var(--ambient-c) 0%, transparent 70%)",
            }}
          />

          {/* Vignette keeps the field from touching the viewport edges. */}
          <div
            className="absolute inset-0"
            style={{
              background:
                "radial-gradient(ellipse 120% 80% at 50% 40%, transparent 40%, var(--background) 100%)",
            }}
          />
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
