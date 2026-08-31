"use client";

import { AnimatePresence, motion } from "motion/react";
import { PanelLeft } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Composer, type ComposerHandle } from "@/components/conversation/composer";
import { ConsoleHome } from "@/components/conversation/console-home";
import { Ledger } from "@/components/conversation/ledger";
import { AmbientField } from "@/components/layout/ambient-field";
import { Rail } from "@/components/layout/rail";
import { ProvenancePanel } from "@/components/provenance/provenance-panel";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useConversation } from "@/hooks/use-conversation";
import { useHealth } from "@/hooks/use-health";
import { consoleRise, DUR, EASE_OUT, shellFade } from "@/lib/motion";
import {
  deriveTitle,
  loadThreads,
  removeThread,
  saveThreads,
  upsertThread,
  type ThreadSummary,
} from "@/lib/threads/storage";
import { clearTranscript } from "@/lib/threads/transcript";
import type { AnalyticsResponse } from "@/lib/types/analytics";
import {
  DEFAULT_MODEL_PROFILE,
  isModelProfile,
  type ModelProfile,
} from "@/lib/models/profiles";

const MODEL_PROFILE_STORAGE_KEY = "eda.model-profile:v1";

/**
 * The workspace shell.
 *
 * Two states share one surface. CONSOLE centres the composer over an ambient
 * field; LEDGER moves the composer to a dock and hands the space to the data.
 * The transition between them is a lift-and-settle rather than a navigation, so
 * asking the first question feels continuous.
 *
 * This component owns conversation state, local thread metadata, and panel
 * visibility. Nothing below it fetches — all network access goes through the
 * API layer via the hooks.
 */
export function Workspace() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [railExpanded, setRailExpanded] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailsFor, setDetailsFor] = useState<AnalyticsResponse | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [modelProfile, setModelProfile] = useState<ModelProfile>(
    DEFAULT_MODEL_PROFILE,
  );

  const composerRef = useRef<ComposerHandle>(null);
  const health = useHealth();

  // localStorage is unavailable during SSR; hydrate after mount.
  useEffect(() => setThreads(loadThreads()), []);
  useEffect(() => {
    const stored = sessionStorage.getItem(MODEL_PROFILE_STORAGE_KEY);
    if (isModelProfile(stored)) setModelProfile(stored);
  }, []);

  const handleModelProfileChange = useCallback((profile: ModelProfile) => {
    setModelProfile(profile);
    sessionStorage.setItem(MODEL_PROFILE_STORAGE_KEY, profile);
  }, []);

  const onThreadEstablished = useCallback(
    (threadId: string, question: string) => {
      setThreads((current) => {
        const next = upsertThread(current, {
          threadId,
          title: deriveTitle(question),
        });
        saveThreads(next);
        return next;
      });
    },
    [],
  );

  const {
    exchanges,
    threadId,
    isBusy,
    ask,
    retry,
    cancel,
    startNewAnalysis,
    resumeThread,
  } = useConversation({ onThreadEstablished });

  const openDetails = useCallback((response: AnalyticsResponse) => {
    setDetailsFor(response);
    setDetailsOpen(true);
  }, []);

  const handleNewAnalysis = useCallback(() => {
    startNewAnalysis();
    setDrawerOpen(false);
    composerRef.current?.focus();
  }, [startNewAnalysis]);

  const handleSelectThread = useCallback(
    (nextThreadId: string) => {
      resumeThread(nextThreadId);
      setDrawerOpen(false);
      composerRef.current?.focus();
    },
    [resumeThread],
  );

  const handleDeleteThread = useCallback(
    (target: string) => {
      setThreads((current) => {
        const next = removeThread(current, target);
        saveThreads(next);
        return next;
      });
      // Removing a thread from the list must also drop its stored results,
      // otherwise deleted analyses linger in session storage.
      clearTranscript(target);
      if (target === threadId) startNewAnalysis();
    },
    [threadId, startNewAnalysis],
  );

  const handleAsk = useCallback(
    (question: string) => void ask(question, modelProfile),
    [ask, modelProfile],
  );

  const isResumed = threadId !== null && exchanges.length === 0;
  const isConsole = exchanges.length === 0 && !isResumed;
  const offline = health.status === "offline";

  const railProps = {
    threads,
    activeThreadId: threadId,
    health,
    onNewAnalysis: handleNewAnalysis,
    onSelectThread: handleSelectThread,
    onDeleteThread: handleDeleteThread,
  };

  return (
    <motion.div
      variants={shellFade}
      initial="hidden"
      animate="visible"
      className="relative flex h-[100dvh] overflow-hidden bg-background"
    >
      {/* Desktop rail */}
      <aside className="z-20 hidden shrink-0 border-e border-sidebar-border md:block">
        <Rail
          {...railProps}
          expanded={railExpanded}
          onExpandedChange={setRailExpanded}
        />
      </aside>

      {/* Mobile drawer */}
      <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
        <SheetContent side="left" className="w-[276px] p-0">
          <SheetTitle className="sr-only">Workspace navigation</SheetTitle>
          <Rail
            {...railProps}
            variant="drawer"
            expanded
            onExpandedChange={() => undefined}
          />
        </SheetContent>
      </Sheet>

      <div className="relative flex min-w-0 flex-1 flex-col">
        <AmbientField visible={isConsole} />

        {/* Mobile-only bar. Desktop needs no header — the rail carries identity
            and the analysis itself supplies the title. */}
        <div className="relative z-10 flex h-14 shrink-0 items-center gap-2 px-3 md:hidden">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className="grid size-9 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <PanelLeft className="size-4" aria-hidden="true" />
            <span className="sr-only">Open navigation</span>
          </button>
          <span className="truncate text-[13px] font-medium">
            Data Intelligence
          </span>
        </div>

        <AnimatePresence mode="wait" initial={false}>
          {isConsole ? (
            /* ---------------------------------------------------- CONSOLE */
            <motion.main
              key="console"
              variants={consoleRise}
              initial="hidden"
              animate="visible"
              exit="exit"
              className="relative z-10 flex min-h-0 flex-1 items-center justify-center overflow-y-auto py-10"
            >
              <ConsoleHome
                onSelect={handleAsk}
                composer={
                  <Composer
                    handleRef={composerRef}
                    onSubmit={handleAsk}
                    onStop={cancel}
                    isBusy={isBusy}
                    disabled={offline}
                    tone="focal"
                    modelProfile={modelProfile}
                    onModelProfileChange={handleModelProfileChange}
                  />
                }
              />
            </motion.main>
          ) : (
            /* ----------------------------------------------------- LEDGER */
            <motion.div
              key="ledger"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: DUR.slow, ease: EASE_OUT }}
              className="relative z-10 flex min-h-0 flex-1 flex-col"
            >
              <main
                data-conversation-scroll
                className="min-h-0 flex-1 overflow-y-auto"
              >
                <div className="mx-auto w-full max-w-[58rem] px-5 py-10 sm:px-8">
                  {isResumed ? (
                    <p className="rounded-lg border border-dashed border-border px-4 py-3 text-[13px] text-muted-foreground">
                      Continuing an earlier analysis. Its context lives on the
                      server — ask a follow-up to pick up where you left off.
                    </p>
                  ) : null}

                  {exchanges.length > 0 ? (
                    <Ledger
                      exchanges={exchanges}
                      onRetry={(id) => void retry(id)}
                      onOpenDetails={openDetails}
                      isBusy={isBusy}
                    />
                  ) : null}
                </div>
              </main>

              {/* Docked composer. The gradient veil lets content scroll under
                  it without a hard edge. */}
              <div className="relative shrink-0">
                <div
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-x-0 -top-8 h-8 bg-gradient-to-t from-background to-transparent"
                />
                <div className="border-t border-border bg-background">
                  <div className="mx-auto w-full max-w-[58rem] px-5 py-3.5 sm:px-8">
                    {offline ? (
                      <p
                        role="status"
                        className="mb-2.5 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-[13px] text-foreground"
                      >
                        The analytics service is unreachable. Questions cannot be
                        sent until it is back.
                      </p>
                    ) : null}
                    <Composer
                      handleRef={composerRef}
                      onSubmit={handleAsk}
                      onStop={cancel}
                      isBusy={isBusy}
                      disabled={offline}
                      tone="docked"
                      placeholder="Ask a follow-up…"
                      modelProfile={modelProfile}
                      onModelProfileChange={handleModelProfileChange}
                    />
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <ProvenancePanel
        response={detailsFor}
        open={detailsOpen}
        onOpenChange={setDetailsOpen}
      />
    </motion.div>
  );
}
