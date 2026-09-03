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
import {
  DEFAULT_DATA_SOURCE,
  DEFAULT_DATA_SOURCE_ID,
  parseDataSources,
  type DataSourceSummary,
} from "@/lib/datasources/datasources";
import { useConversation } from "@/hooks/use-conversation";
import { useHealth } from "@/hooks/use-health";
import { consoleRise, DUR, EASE_OUT, shellFade } from "@/lib/motion";
import {
  archiveConversation,
  fetchConversations,
  type ConversationSummary,
} from "@/lib/conversations/api";
import type { AnalyticsResponse } from "@/lib/types/analytics";
import {
  DEFAULT_MODEL_PROFILE,
  isModelProfile,
  type ModelProfile,
} from "@/lib/models/profiles";

const MODEL_PROFILE_STORAGE_KEY = "eda.model-profile:v1";
const DATA_SOURCE_STORAGE_KEY = "eda.data-source:v1";
/**
 * Which conversation is open — an id, never its contents.
 *
 * Leaving for the knowledge area and coming back remounts this component, and
 * without a pointer the reader lands on the console home wondering where their
 * analysis went. The transcript itself is still fetched from the server every
 * time; this only records which one to ask for.
 */
const OPEN_CONVERSATION_STORAGE_KEY = "eda.conversation:v1";

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
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [railExpanded, setRailExpanded] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailsFor, setDetailsFor] = useState<AnalyticsResponse | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [modelProfile, setModelProfile] = useState<ModelProfile>(
    DEFAULT_MODEL_PROFILE,
  );
  const [dataSources, setDataSources] = useState<DataSourceSummary[]>([
    DEFAULT_DATA_SOURCE,
  ]);
  const [dataSourceId, setDataSourceId] = useState<string>(
    DEFAULT_DATA_SOURCE_ID,
  );

  const [conversationsFailed, setConversationsFailed] = useState(false);

  // Set once the hook exists, so `onThreadEstablished` can reach it without
  // the two definitions depending on each other.
  const adoptRef = useRef<((conversationId: string) => void) | null>(null);
  const composerRef = useRef<ComposerHandle>(null);
  const health = useHealth();

  const loadConversations = useCallback(async () => {
    try {
      const listed = await fetchConversations();
      setConversations(listed);
      return listed;
    } catch {
      // History is unavailable. Asking a new question still works, and the
      // sidebar says the list could not be loaded rather than claiming empty.
      setConversationsFailed(true);
      return [];
    }
  }, []);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    const stored = sessionStorage.getItem(MODEL_PROFILE_STORAGE_KEY);
    if (isModelProfile(stored)) setModelProfile(stored);
    const storedSource = sessionStorage.getItem(DATA_SOURCE_STORAGE_KEY);
    if (storedSource !== null && storedSource !== "") {
      setDataSourceId(storedSource);
    }
  }, []);

  // Listing datasources needs review authority. An ordinary analyst gets 403,
  // which is a normal outcome rather than an error: they keep querying the
  // default database and simply see no picker.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch("/api/backend/knowledge/data-sources", {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) return;
        const parsed = parseDataSources(await response.json());
        if (!cancelled && parsed.length > 0) setDataSources(parsed);
      } catch {
        // Offline or unavailable: the default datasource still works.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleModelProfileChange = useCallback((profile: ModelProfile) => {
    setModelProfile(profile);
    sessionStorage.setItem(MODEL_PROFILE_STORAGE_KEY, profile);
  }, []);

  // The server creates the conversation as part of answering, so the sidebar
  // is refreshed from it rather than guessing what it now contains. The new
  // row is matched by thread, which is the stable link between the analytical
  // context and the transcript that records it.
  const onThreadEstablished = useCallback(
    (establishedThreadId: string) => {
      void (async () => {
        const listed = await loadConversations();
        const created = listed.find(
          (conversation) => conversation.threadId === establishedThreadId,
        );
        if (created === undefined) return;
        adoptRef.current?.(created.id);
        sessionStorage.setItem(OPEN_CONVERSATION_STORAGE_KEY, created.id);
      })();
    },
    [loadConversations],
  );

  const {
    exchanges,
    threadId,
    conversationId,
    isBusy,
    isRestoring,
    restoreNotice,
    ask,
    retry,
    cancel,
    startNewAnalysis,
    openConversation,
    adoptConversation,
  } = useConversation({ onThreadEstablished });

  adoptRef.current = adoptConversation;

  /**
   * Open a conversation and follow it to its database.
   *
   * A conversation belongs to one datasource. Restoring the transcript without
   * also selecting that datasource would leave the composer pointed somewhere
   * else, and the next follow-up would run against a database this thread has
   * never touched.
   */
  const restoreConversation = useCallback(
    async (nextConversationId: string) => {
      const source = await openConversation(nextConversationId);
      if (source === null || source === "") return;
      setDataSourceId(source);
      sessionStorage.setItem(DATA_SOURCE_STORAGE_KEY, source);
    },
    [openConversation],
  );

  // Reopen whatever was open before this component last unmounted — leaving
  // for the knowledge area and coming back should not look like the analysis
  // was thrown away. The transcript is fetched from the server either way.
  useEffect(() => {
    const open = sessionStorage.getItem(OPEN_CONVERSATION_STORAGE_KEY);
    if (open !== null && open !== "") void restoreConversation(open);
    // Once, on mount: re-running would clobber a conversation opened since.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openDetails = useCallback((response: AnalyticsResponse) => {
    setDetailsFor(response);
    setDetailsOpen(true);
  }, []);

  const handleNewAnalysis = useCallback(() => {
    sessionStorage.removeItem(OPEN_CONVERSATION_STORAGE_KEY);
    startNewAnalysis();
    setDrawerOpen(false);
    composerRef.current?.focus();
  }, [startNewAnalysis]);

  const handleSelectConversation = useCallback(
    (nextConversationId: string) => {
      sessionStorage.setItem(
        OPEN_CONVERSATION_STORAGE_KEY,
        nextConversationId,
      );
      void restoreConversation(nextConversationId);
      setDrawerOpen(false);
      composerRef.current?.focus();
    },
    [restoreConversation],
  );

  const handleArchiveConversation = useCallback(
    async (target: string) => {
      // Optimistic, because the row disappearing is the whole feedback. A
      // failure puts it back by reloading the authoritative list.
      setConversations((current) =>
        current.filter((conversation) => conversation.id !== target),
      );
      if (target === conversationId) startNewAnalysis();
      try {
        await archiveConversation(target);
      } finally {
        await loadConversations();
      }
    },
    [conversationId, loadConversations, startNewAnalysis],
  );

  const handleAsk = useCallback(
    (question: string) => void ask(question, modelProfile, dataSourceId),
    [ask, modelProfile, dataSourceId],
  );

  const handleDataSourceChange = useCallback(
    (next: string) => {
      if (next === dataSourceId) return;
      setDataSourceId(next);
      sessionStorage.setItem(DATA_SOURCE_STORAGE_KEY, next);
      // A thread belongs to one database. Continuing the current thread against
      // a different one would let the previous answer act as context for a
      // database it never touched, so switching starts a fresh analysis.
      startNewAnalysis();
    },
    [dataSourceId, startNewAnalysis],
  );

  // A conversation is open as soon as one is selected, even before its
  // transcript arrives — otherwise restoring flashes the console home.
  const isConsole =
    exchanges.length === 0 &&
    !isRestoring &&
    conversationId === null &&
    threadId === null;
  const offline = health.status === "offline";

  const railProps = {
    conversations,
    conversationsFailed,
    activeConversationId: conversationId,
    health,
    onNewAnalysis: handleNewAnalysis,
    onSelectConversation: handleSelectConversation,
    onArchiveConversation: (target: string) =>
      void handleArchiveConversation(target),
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
                    dataSourceId={dataSourceId}
                    dataSources={dataSources}
                    onDataSourceChange={handleDataSourceChange}
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
                  {isRestoring ? <TranscriptSkeleton /> : null}

                  {restoreNotice !== null ? (
                    <p className="rounded-lg border border-dashed border-border px-4 py-3 text-[13px] text-muted-foreground">
                      {restoreNotice}
                    </p>
                  ) : null}

                  {exchanges.length > 0 ? (
                    <Ledger
                      exchanges={exchanges}
                      onRetry={(id) => void retry(id)}
                      onOpenDetails={openDetails}
                      onAsk={handleAsk}
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
                      dataSourceId={dataSourceId}
                      dataSources={dataSources}
                      onDataSourceChange={handleDataSourceChange}
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

/**
 * Placeholder turns while a stored transcript loads.
 *
 * Restoring reads rows rather than calling a model, so this is brief — but a
 * blank flash followed by content jumping in reads as a bug, and the shapes
 * here match the exchanges that replace them.
 */
function TranscriptSkeleton() {
  return (
    <div aria-hidden="true" className="space-y-8">
      {[0, 1].map((index) => (
        <div key={index} className="space-y-3">
          <div className="h-4 w-2/5 animate-pulse rounded bg-muted" />
          <div className="h-3 w-full animate-pulse rounded bg-muted/70" />
          <div className="h-3 w-4/5 animate-pulse rounded bg-muted/70" />
          <div className="h-28 w-full animate-pulse rounded-lg bg-muted/50" />
        </div>
      ))}
    </div>
  );
}
