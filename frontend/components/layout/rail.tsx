"use client";

import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookMarked, MessagesSquare, PanelLeftClose, Plus, Trash2 } from "lucide-react";


import { SystemStatus } from "@/components/layout/system-status";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { SystemHealth } from "@/hooks/use-health";
import { DUR, EASE_OUT, SPRING } from "@/lib/motion";
import {
  groupConversations,
  type ConversationSummary,
} from "@/lib/conversations/api";
import { cn } from "@/lib/utils";

/**
 * Navigation rail.
 *
 * Collapsed it is a 56px column of glyphs; expanded it becomes a 252px panel of
 * recent analyses. Following Linear's principle that navigation should recede
 * while the work stays in focus, the collapsed rail is the default on desktop
 * and the expanded state is summoned rather than permanent.
 *
 * The conversation list comes from the server, so it is the same list on any
 * browser the user signs in from and survives a restart of either process.
 */

const RAIL_W = 56;
const PANEL_W = 252;

/** Where the rail can take you. Reviewing is a separate job from asking. */
const DESTINATIONS = [
  { href: "/", label: "Workspace", icon: MessagesSquare },
  { href: "/knowledge", label: "Knowledge", icon: BookMarked },
] as const;

interface RailProps {
  conversations: ConversationSummary[];
  /** The list could not be read; say so rather than showing an empty state. */
  conversationsFailed: boolean;
  activeConversationId: string | null;
  health: SystemHealth;
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
  onNewAnalysis: () => void;
  onSelectConversation: (conversationId: string) => void;
  onArchiveConversation: (conversationId: string) => void;
  /** Drawer mode on small screens: always expanded, no collapse control. */
  variant?: "rail" | "drawer";
}

export function Rail({
  conversations,
  conversationsFailed,
  activeConversationId,
  health,
  expanded,
  onExpandedChange,
  onNewAnalysis,
  onSelectConversation,
  onArchiveConversation,
  variant = "rail",
}: RailProps) {
  const isDrawer = variant === "drawer";
  const pathname = usePathname();
  const open = isDrawer || expanded;
  const groups = groupConversations(conversations);

  return (
    <motion.div
      animate={{ width: isDrawer ? "100%" : open ? PANEL_W : RAIL_W }}
      initial={false}
      transition={SPRING}
      className="relative flex h-full flex-col overflow-hidden bg-sidebar"
      onMouseLeave={() => {
        if (!isDrawer && expanded) onExpandedChange(false);
      }}
    >
      {/* Identity + expand control */}
      <div className="flex h-14 shrink-0 items-center gap-1 px-2">
        <button
          type="button"
          onClick={() => !isDrawer && onExpandedChange(!expanded)}
          onMouseEnter={() => !isDrawer && onExpandedChange(true)}
          aria-expanded={open}
          aria-label={open ? "Collapse navigation" : "Expand navigation"}
          className="group relative grid size-10 shrink-0 place-items-center rounded-lg transition-colors hover:bg-sidebar-accent"
        >
          {/* Mark: a stacked-strata glyph — layers of data, not a letter tile. */}
          <svg
            viewBox="0 0 20 20"
            className="size-[18px]"
            aria-hidden="true"
            fill="none"
          >
            <path
              d="M10 2.5 17.5 6.5 10 10.5 2.5 6.5 10 2.5Z"
              fill="var(--sidebar-primary)"
            />
            <path
              d="M3.6 10 10 13.4 16.4 10"
              stroke="var(--sidebar-primary)"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              opacity="0.62"
            />
            <path
              d="M3.6 13.6 10 17 16.4 13.6"
              stroke="var(--sidebar-primary)"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              opacity="0.32"
            />
          </svg>
        </button>

        <AnimatePresence initial={false}>
          {open ? (
            <motion.div
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -6 }}
              transition={{ duration: DUR.fast, ease: EASE_OUT }}
              className="flex min-w-0 flex-1 items-center gap-1"
            >
              <span className="min-w-0 flex-1 truncate text-[13px] font-semibold tracking-tight text-sidebar-foreground">
                Data Intelligence
              </span>
              {isDrawer ? null : (
                <button
                  type="button"
                  onClick={() => onExpandedChange(false)}
                  aria-label="Collapse navigation"
                  className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
                >
                  <PanelLeftClose className="size-4" aria-hidden="true" />
                </button>
              )}
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>

      {/* New analysis */}
      <div className="px-2 pb-2">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={onNewAnalysis}
              className={cn(
                "group flex h-10 w-full items-center gap-2.5 rounded-lg text-[13px] font-medium transition-all",
                "bg-primary/10 text-primary hover:bg-primary/15",
                "active:scale-[0.98]",
                open ? "px-2.5" : "justify-center px-0",
              )}
            >
              <Plus className="size-4 shrink-0" aria-hidden="true" />
              <AnimatePresence initial={false}>
                {open ? (
                  <motion.span
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: DUR.fast }}
                    className="truncate"
                  >
                    New analysis
                  </motion.span>
                ) : null}
              </AnimatePresence>
              <span className="sr-only">{open ? "" : "New analysis"}</span>
            </button>
          </TooltipTrigger>
          {open ? null : (
            <TooltipContent side="right">New analysis</TooltipContent>
          )}
        </Tooltip>
      </div>

      {/* Destinations. The knowledge area was reachable only by typing its
          URL: the route existed, the console existed, and nothing linked to
          it, so the review queue was invisible to the person meant to work
          it. Whether its contents are permitted is still decided by the
          backend -- an unauthorized reviewer sees the page explain that. */}
      <nav aria-label="Sections" className="px-2 pb-2">
        <ul className="space-y-px">
          {DESTINATIONS.map((destination) => {
            const active =
              destination.href === "/"
                ? pathname === "/"
                : pathname.startsWith(destination.href);
            const Icon = destination.icon;
            return (
              <li key={destination.href}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Link
                      href={destination.href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "relative flex h-9 w-full items-center gap-2.5 rounded-lg text-[13px] transition-colors",
                        active
                          ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                          : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                        open ? "px-2.5" : "justify-center px-0",
                      )}
                    >
                      {active ? (
                        <motion.span
                          layoutId="destination-active"
                          transition={SPRING}
                          aria-hidden="true"
                          className="absolute inset-y-1.5 start-0 w-[2px] rounded-full bg-primary"
                        />
                      ) : null}
                      <Icon className="size-4 shrink-0" aria-hidden="true" />
                      <AnimatePresence initial={false}>
                        {open ? (
                          <motion.span
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            transition={{ duration: DUR.fast }}
                            className="truncate"
                          >
                            {destination.label}
                          </motion.span>
                        ) : null}
                      </AnimatePresence>
                      <span className="sr-only">{open ? "" : destination.label}</span>
                    </Link>
                  </TooltipTrigger>
                  {open ? null : (
                    <TooltipContent side="right">{destination.label}</TooltipContent>
                  )}
                </Tooltip>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Conversations */}
      <nav
        aria-label="Conversations"
        className={cn(
          "min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-2",
          open ? "" : "pointer-events-none opacity-0",
        )}
        aria-hidden={open ? undefined : true}
      >
        {groups.length === 0 ? (
          <p className="px-2 py-3 text-[12px] leading-relaxed text-muted-foreground">
            {conversationsFailed
              ? "Conversation history could not be loaded."
              : "Your conversations appear here once you ask a question."}
          </p>
        ) : (
          groups.map((group) => (
            <div key={group.label} className="mb-4">
              <h2 className="label-xs px-2 pb-1.5 text-muted-foreground">
                {group.label}
              </h2>
              <ul className="space-y-px">
                {group.conversations.map((conversation) => {
                  const active = conversation.id === activeConversationId;
                  return (
                    <li key={conversation.id} className="group/thread relative">
                      <button
                        type="button"
                        onClick={() => onSelectConversation(conversation.id)}
                        aria-current={active ? "true" : undefined}
                        title={conversation.title}
                        className={cn(
                          "relative flex w-full items-center rounded-md py-1.5 pe-7 ps-2.5 text-start text-[13px] transition-colors",
                          active
                            ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                            : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                        )}
                      >
                        {active ? (
                          <motion.span
                            layoutId="thread-active"
                            transition={SPRING}
                            aria-hidden="true"
                            className="absolute inset-y-1 start-0 w-[2px] rounded-full bg-primary"
                          />
                        ) : null}
                        <span dir="auto" className="min-w-0 flex-1 truncate">
                          {conversation.title}
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => onArchiveConversation(conversation.id)}
                        className="absolute end-1 top-1/2 grid size-6 -translate-y-1/2 place-items-center rounded text-muted-foreground opacity-0 transition-all hover:bg-background hover:text-destructive focus-visible:opacity-100 group-hover/thread:opacity-100"
                      >
                        <Trash2 className="size-3.5" aria-hidden="true" />
                        <span className="sr-only">
                          Archive {conversation.title}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
        )}
      </nav>

      {/* Theme + status. Kept in the footer so both stay reachable while the
          rail is collapsed — a control that only exists when expanded is a
          keyboard trap for anyone who never expands it. */}
      <div
        className={cn(
          "mt-auto flex shrink-0 items-center gap-1 border-t border-sidebar-border p-2",
          open ? "" : "flex-col",
        )}
      >
        <SystemStatus health={health} compact={!open} />
        <ThemeToggle />
      </div>
    </motion.div>
  );
}
