import type { Transition, Variants } from "motion/react";

/**
 * The motion vocabulary.
 *
 * Every animation in the product resolves to one of these. Motion here always
 * carries meaning — hierarchy, cause and effect, or spatial relationship — and
 * only ever drives `transform` and `opacity` so it stays off the layout path.
 *
 * Durations mirror the CSS custom properties in `globals.css`.
 */

export const DUR = {
  instant: 0.09,
  fast: 0.15,
  base: 0.24,
  slow: 0.4,
} as const;

/** Quick departure, soft landing. The workspace default. */
export const EASE_OUT = [0.22, 1, 0.36, 1] as const;
export const EASE_ENTRANCE = [0.16, 1, 0.3, 1] as const;

/** Panels and the rail: weighted, physical, never bouncy. */
export const SPRING: Transition = {
  type: "spring",
  stiffness: 420,
  damping: 38,
  mass: 0.9,
};

export const SPRING_SOFT: Transition = {
  type: "spring",
  stiffness: 260,
  damping: 30,
  mass: 1,
};

/**
 * A result reveals in reading order: insight, then evidence, then trust.
 * The stagger is what makes an answer feel composed rather than dumped.
 */
export const revealParent: Variants = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.075, delayChildren: 0.04 },
  },
};

export const revealChild: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: DUR.slow, ease: EASE_ENTRANCE },
  },
};

/** Staged entrance for the shell on first paint. */
export const shellFade: Variants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { duration: DUR.slow, ease: EASE_OUT },
  },
};

/** The console focal point arrives with a little more travel than a result. */
export const consoleRise: Variants = {
  hidden: { opacity: 0, y: 16, scale: 0.985 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: 0.55, ease: EASE_ENTRANCE },
  },
  exit: {
    opacity: 0,
    y: -12,
    scale: 0.99,
    transition: { duration: DUR.base, ease: EASE_OUT },
  },
};

/** A new exchange entering the ledger. */
export const entryEnter: Variants = {
  hidden: { opacity: 0, y: 12 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: DUR.slow, ease: EASE_ENTRANCE },
  },
};
