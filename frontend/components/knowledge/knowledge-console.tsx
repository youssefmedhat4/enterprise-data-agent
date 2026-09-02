"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { MotionConfig, motion } from "motion/react";
import { Tabs as TabsPrimitive } from "radix-ui";
import {
  AlertTriangle,
  CalendarClock,
  ClipboardCheck,
  Database,
  FileCode2,
  Gauge,
  Layers,
  LayoutGrid,
  Lightbulb,
  Plus,
  Repeat,
  Shapes,
  ShieldCheck,
} from "lucide-react";

import { EvaluationsPanel } from "@/components/knowledge/evaluations-panel";
import { QualityPanel } from "@/components/knowledge/quality-panel";
import { DatasourceCard } from "@/components/knowledge/shell/datasource-card";
import {
  KnowledgeNav,
  type KnowledgeSection,
} from "@/components/knowledge/shell/knowledge-nav";
import { KnowledgeOverview } from "@/components/knowledge/shell/knowledge-overview";
import {
  CardSkeleton,
  DetailRow,
  EmptyState,
  Panel,
  SectionHeader,
  SectionTransition,
  StatusBadge,
  toneForStatus,
} from "@/components/knowledge/shell/primitives";
import { CandidateCard } from "@/components/knowledge/shell/review-card";
import { SchemaReview } from "@/components/knowledge/shell/schema-review";
import { TimePanel } from "@/components/knowledge/time-panel";
import { Button } from "@/components/ui/button";
import { DUR, EASE_ENTRANCE } from "@/lib/motion";
import {
  DEFAULT_DATA_SOURCE,
  DEFAULT_DATA_SOURCE_ID,
  type DataSourceSummary,
} from "@/lib/datasources/datasources";
import {
  fetchCandidates,
  fetchCertifiedMetrics,
  fetchClusters,
  fetchColumnPreviews,
  fetchConnectionRefs,
  fetchQueryExamples,
  fetchSemantics,
  KnowledgeAccessError,
  registerDataSource,
  reindexDataSource,
  reviewCandidate,
  reviewSemantic,
  scanDataSource,
  type CertifiedMetric,
  type KnowledgeCandidate,
  type KnowledgeCluster,
  type QueryExample,
  type SemanticProposal,
} from "@/lib/knowledge/knowledge";

interface KnowledgeConsoleProps {
  dataSourceId?: string;
  dataSources?: readonly DataSourceSummary[];
  /** Lets the workspace refresh its selector after a registration. */
  onDataSourcesChanged?: () => Promise<void> | void;
}

/**
 * Reviewer surface for everything the system has learned about one database.
 *
 * Read-mostly by design. The only mutations are review decisions, and those all
 * call the backend, which re-validates before certifying — the UI never changes
 * a status on its own.
 *
 * The ten areas are grouped by the question they answer: what this database
 * *is*, what has been *learned* from using it, and whether any of that can be
 * *trusted*. That ordering is the navigation, and it is also roughly the order
 * a new database is brought into service.
 */
export function KnowledgeConsole({
  dataSourceId = DEFAULT_DATA_SOURCE_ID,
  dataSources = [DEFAULT_DATA_SOURCE],
  onDataSourcesChanged,
}: KnowledgeConsoleProps) {
  const [section, setSection] = useState("overview");
  const [clusters, setClusters] = useState<KnowledgeCluster[]>([]);
  const [candidates, setCandidates] = useState<KnowledgeCandidate[]>([]);
  const [metrics, setMetrics] = useState<CertifiedMetric[]>([]);
  const [examples, setExamples] = useState<QueryExample[]>([]);
  const [semantics, setSemantics] = useState<SemanticProposal[]>([]);
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [connectionRefs, setConnectionRefs] = useState<string[]>([]);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({
    name: "",
    databaseType: "postgres",
    connectionRef: "",
  });
  const [denied, setDenied] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [previews, setPreviews] = useState<Record<string, string[]>>({});
  const [previewsFor, setPreviewsFor] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [
        nextClusters,
        nextCandidates,
        nextMetrics,
        nextExamples,
        nextSemantics,
      ] = await Promise.all([
        fetchClusters(dataSourceId),
        fetchCandidates(dataSourceId),
        fetchCertifiedMetrics(dataSourceId),
        fetchQueryExamples(dataSourceId),
        fetchSemantics(dataSourceId),
      ]);
      setClusters(nextClusters);
      setCandidates(nextCandidates);
      setMetrics(nextMetrics);
      setExamples(nextExamples);
      setSemantics(nextSemantics);
      setDenied(false);
    } catch (error) {
      if (error instanceof KnowledgeAccessError && error.status === 403) {
        setDenied(true);
        return;
      }
      setNotice("The knowledge service is unavailable.");
    } finally {
      setLoading(false);
    }
  }, [dataSourceId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setPreviews({});
    setPreviewsFor(null);
  }, [dataSourceId]);

  /**
   * Example values are fetched only once schema review is actually open.
   *
   * They cost a connection and a bounded read of the datasource, which is
   * worth it for the person deciding what a column means and worth nothing to
   * anyone looking at certified metrics.
   */
  useEffect(() => {
    if (section !== "review" || previewsFor === dataSourceId) return;
    setPreviewsFor(dataSourceId);
    void fetchColumnPreviews(dataSourceId).then(setPreviews);
  }, [dataSourceId, previewsFor, section]);

  useEffect(() => {
    void fetchConnectionRefs().then((refs) => {
      setConnectionRefs(refs);
      setForm((current) =>
        current.connectionRef === "" && refs.length > 0
          ? { ...current, connectionRef: refs[0] }
          : current,
      );
    });
  }, []);

  const review = useCallback(
    async (candidateId: string, action: "approve" | "reject") => {
      setBusyId(candidateId);
      const result = await reviewCandidate(
        dataSourceId,
        candidateId,
        action,
        action === "reject" ? "Rejected by reviewer." : undefined,
      );
      setNotice(result.message);
      setBusyId(null);
      await load();
    },
    [dataSourceId, load],
  );

  const reviewMapping = useCallback(
    async (proposalId: string, action: "approve" | "reject") => {
      setBusyId(proposalId);
      const corrected = editing[proposalId]?.trim();
      const result = await reviewSemantic(
        dataSourceId,
        proposalId,
        action,
        action === "approve" && corrected ? corrected : undefined,
      );
      setNotice(result.message);
      setBusyId(null);
      await load();
    },
    [dataSourceId, editing, load],
  );

  /**
   * Approve a set the reviewer picked, one call each.
   *
   * Deliberately not a batch endpoint: every approval goes through the same
   * route, the same authority check and the same validation as a single one,
   * so selecting several is a convenience for the reviewer and never a
   * different kind of decision.
   */
  const approveMany = useCallback(
    async (ids: readonly string[]) => {
      setBusyId("bulk");
      let approved = 0;
      for (const id of ids) {
        const corrected = editing[id]?.trim();
        const result = await reviewSemantic(
          dataSourceId,
          id,
          "approve",
          corrected ? corrected : undefined,
        );
        if (result.ok) approved += 1;
      }
      setNotice(
        "Confirmed " +
          approved +
          " of " +
          ids.length +
          (ids.length === 1 ? " proposal." : " proposals."),
      );
      setBusyId(null);
      await load();
    },
    [dataSourceId, editing, load],
  );

  const scan = useCallback(async () => {
    setBusyId("scan");
    const result = await scanDataSource(dataSourceId);
    setNotice(
      result.summary === null
        ? result.message
        : `Scanned ${result.summary.tableCount} tables · ` +
          `${result.summary.proposedEntities} entity proposals · ` +
          `${result.summary.markedStale} marked stale`,
    );
    setBusyId(null);
    await load();
  }, [dataSourceId, load]);

  const register = useCallback(async () => {
    setBusyId("register");
    const result = await registerDataSource(form);
    setNotice(result.message);
    setBusyId(null);
    if (result.ok) {
      setAdding(false);
      setForm({ name: "", databaseType: "postgres", connectionRef: form.connectionRef });
      await onDataSourcesChanged?.();
    }
    await load();
  }, [form, load, onDataSourcesChanged]);

  const reindex = useCallback(
    async (id: string) => {
      setBusyId(`reindex-${id}`);
      const result = await reindexDataSource(id);
      setNotice(result.message);
      setBusyId(null);
    },
    [],
  );

  const proposals = semantics.filter((item) => item.status === "PROPOSED");
  const confirmed = semantics.filter(
    (item) => item.status === "CONFIRMED" || item.status === "STALE",
  );
  const stale = confirmed.filter((item) => item.status === "STALE");
  const pendingCandidates = candidates.filter(
    (item) => item.status === "PROPOSED",
  );

  const source =
    dataSources.find((candidate) => candidate.id === dataSourceId) ??
    DEFAULT_DATA_SOURCE;

  const sections = useMemo<KnowledgeSection[]>(
    () => [
      { value: "overview", label: "Overview", icon: LayoutGrid, group: "" },
      { value: "sources", label: "Data sources", icon: Database, group: "DATA" },
      {
        value: "review",
        label: "Schema review",
        icon: Shapes,
        group: "DATA",
        count: proposals.length,
        attention: true,
      },
      {
        value: "confirmed",
        label: "Confirmed semantics",
        icon: Layers,
        group: "DATA",
      },
      {
        value: "questions",
        label: "Recurring questions",
        icon: Repeat,
        group: "LEARNING",
      },
      {
        value: "candidates",
        label: "Candidates",
        icon: Lightbulb,
        group: "LEARNING",
        count: pendingCandidates.length,
        attention: true,
      },
      {
        value: "metrics",
        label: "Certified metrics",
        icon: Gauge,
        group: "LEARNING",
      },
      {
        value: "examples",
        label: "Approved examples",
        icon: FileCode2,
        group: "LEARNING",
      },
      {
        value: "evaluations",
        label: "Evaluations",
        icon: ClipboardCheck,
        group: "TRUST",
      },
      {
        value: "quality",
        label: "Data quality",
        icon: ShieldCheck,
        group: "TRUST",
      },
      {
        value: "time",
        label: "Time intelligence",
        icon: CalendarClock,
        group: "TRUST",
      },
    ],
    [pendingCandidates.length, proposals.length],
  );

  if (denied) {
    return (
      <section className="mx-auto max-w-xl px-6 py-24 text-center">
        <div className="mx-auto grid size-11 place-items-center rounded-xl bg-warning/12">
          <AlertTriangle className="size-5 text-warning" aria-hidden="true" />
        </div>
        <h1 className="mt-5 text-[19px] font-semibold tracking-tight">
          Review authority required
        </h1>
        <p className="measure mx-auto mt-2 text-[13.5px] leading-relaxed text-muted-foreground">
          Reviewing semantics and certifying metrics is separate from analytics
          access. Ask an administrator to grant knowledge review.
        </p>
      </section>
    );
  }

  return (
    /* `reducedMotion="user"` is what makes the entrances above respect the
       system setting: the CSS guard in globals.css only reaches CSS
       transitions, not animations Framer drives from JavaScript. */
    <MotionConfig reducedMotion="user">
      <motion.section
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: DUR.slow, ease: EASE_ENTRANCE }}
        className="mx-auto w-full max-w-[1440px] px-6 py-10 lg:px-10 lg:py-12"
      >
        {/* ------------------------------------------------------------ header */}
        <header>
          <h1 className="text-[26px] font-semibold tracking-tight text-foreground">
            Knowledge
          </h1>
          <p className="measure mt-2 text-[14px] leading-relaxed text-muted-foreground">
            What the system understands about this database, what it is allowed to
            say, and the evidence that it still answers correctly.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-hairline bg-surface px-2.5 py-1 text-[12.5px] text-foreground">
              <Database className="size-3.5 text-muted-foreground" aria-hidden="true" />
              {source.name}
            </span>
            <span className="inline-flex items-center rounded-full border border-hairline bg-surface px-2.5 py-1 text-[12.5px] text-muted-foreground">
              {source.databaseType}
            </span>
            <StatusBadge tone={toneForStatus(source.status)} dot>
              {source.status}
            </StatusBadge>
          </div>
        </header>

        {notice !== null ? (
          <motion.p
            role="status"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: DUR.base, ease: EASE_ENTRANCE }}
            className="mt-6 rounded-lg border border-hairline bg-surface px-3.5 py-2.5 text-[13px] text-foreground"
          >
            {notice}
          </motion.p>
        ) : null}

        {/* ------------------------------------------------- navigation + body */}
        <TabsPrimitive.Root
          value={section}
          onValueChange={setSection}
          orientation="vertical"
          className="mt-8 flex flex-col gap-5 lg:flex-row lg:gap-10"
        >
          <KnowledgeNav
            sections={sections}
            value={section}
            onValueChange={setSection}
          />

          <div className="min-w-0 max-w-[1180px] flex-1">
            {/* ------------------------------------------------------ overview */}
            <TabsPrimitive.Content value="overview" className="outline-none">
              <SectionTransition>
                <KnowledgeOverview
                  source={source}
                  loading={loading}
                  onNavigate={setSection}
                  counts={{
                    proposals: proposals.length,
                    pendingCandidates: pendingCandidates.length,
                    stale: stale.length,
                    confirmed: confirmed.length,
                    metrics: metrics.length,
                    examples: examples.length,
                    clusters: clusters.length,
                  }}
                />
              </SectionTransition>
            </TabsPrimitive.Content>

            {/* -------------------------------------------------- data sources */}
            <TabsPrimitive.Content value="sources" className="outline-none">
              <SectionTransition>
                <SectionHeader
                  title="Data sources"
                  description="Databases this workspace can query. Scanning discovers what their tables mean; reindexing rebuilds the semantic search over what is already known."
                  action={
                    <Button
                      size="sm"
                      variant={adding ? "ghost" : "outline"}
                      onClick={() => setAdding((current) => !current)}
                    >
                      <Plus className="size-3.5" aria-hidden="true" />
                      {adding ? "Cancel" : "Add data source"}
                    </Button>
                  }
                />

                {adding ? (
                  <Panel asChild>
                    <form
                      aria-label="Add data source"
                      className="p-5"
                      onSubmit={(event) => {
                        event.preventDefault();
                        void register();
                      }}
                    >
                      <div className="grid gap-4 sm:grid-cols-3">
                        <Field htmlFor="ds-name" label="Name">
                          <input
                            id="ds-name"
                            required
                            value={form.name}
                            onChange={(event) =>
                              setForm((current) => ({
                                ...current,
                                name: event.target.value,
                              }))
                            }
                            className={FIELD_CLASS}
                          />
                        </Field>
                        <Field htmlFor="ds-type" label="Database type">
                          <select
                            id="ds-type"
                            value={form.databaseType}
                            onChange={(event) =>
                              setForm((current) => ({
                                ...current,
                                databaseType: event.target.value,
                              }))
                            }
                            className={FIELD_CLASS}
                          >
                            <option value="postgres">postgres</option>
                          </select>
                        </Field>
                        <Field htmlFor="ds-connection" label="Connection">
                          {/* A choice, never free text: the server decides which
                              references exist, so no DSN or password can be
                              typed. */}
                          <select
                            id="ds-connection"
                            required
                            value={form.connectionRef}
                            onChange={(event) =>
                              setForm((current) => ({
                                ...current,
                                connectionRef: event.target.value,
                              }))
                            }
                            className={FIELD_CLASS}
                          >
                            {connectionRefs.map((ref) => (
                              <option key={ref} value={ref}>
                                {ref}
                              </option>
                            ))}
                          </select>
                        </Field>
                      </div>
                      <p className="measure mt-4 text-[12.5px] leading-relaxed text-muted-foreground">
                        Connections are configured on the server. Credentials are
                        never entered here and are never sent from the browser.
                      </p>
                      <Button
                        type="submit"
                        size="sm"
                        className="mt-4"
                        disabled={busyId === "register" || connectionRefs.length === 0}
                      >
                        Register
                      </Button>
                    </form>
                  </Panel>
                ) : null}

                <div className="space-y-4">
                  {dataSources.map((entry) => (
                    <DatasourceCard
                      key={entry.id}
                      source={entry}
                      active={entry.id === dataSourceId}
                      busy={busyId !== null}
                      onScan={() => void scan()}
                      onReindex={() => void reindex(entry.id)}
                    />
                  ))}
                </div>
              </SectionTransition>
            </TabsPrimitive.Content>

            {/* ------------------------------------------------- schema review */}
            <TabsPrimitive.Content value="review" className="outline-none">
              <SectionTransition>
                <SectionHeader
                  title="Schema review"
                  count={proposals.length}
                  description="Each proposal reads one table or column and suggests what it means in business terms. Approve it, or rename it first — the name you save is what questions are then understood against."
                />
                {loading ? (
                  <SkeletonList />
                ) : (
                  <SchemaReview
                    proposals={proposals}
                    previews={previews}
                    drafts={editing}
                    busyId={busyId}
                    onDraftChange={(id, value) =>
                      setEditing((current) => ({ ...current, [id]: value }))
                    }
                    onReview={(id, action) => void reviewMapping(id, action)}
                    onApproveMany={approveMany}
                  />
                )}
              </SectionTransition>
            </TabsPrimitive.Content>

            {/* -------------------------------------------- confirmed semantics */}
            <TabsPrimitive.Content value="confirmed" className="outline-none">
              <SectionTransition>
                <SectionHeader
                  title="Confirmed semantics"
                  description="Approved mappings, and the ones that went stale when the schema changed underneath them. A stale mapping is not used until it is re-confirmed."
                />
                {loading ? (
                  <SkeletonList />
                ) : confirmed.length === 0 ? (
                  <EmptyState
                    icon={Layers}
                    title="Nothing confirmed yet"
                    description="Approved mappings appear here and drive how questions are understood."
                  />
                ) : (
                  <div className="space-y-4">
                    {confirmed.map((mapping) => (
                      <Panel interactive key={mapping.id} className="p-5">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <div className="min-w-0">
                            <h3 className="text-[15px] font-medium text-foreground">
                              {mapping.proposedConcept}
                            </h3>
                            <p className="mt-1 font-mono text-[12px] text-muted-foreground">
                              {mapping.physical}
                            </p>
                            {mapping.detail !== "" ? (
                              <p className="measure mt-2 text-[13px] leading-relaxed text-muted-foreground">
                                {mapping.detail}
                              </p>
                            ) : null}
                          </div>
                          <div className="flex shrink-0 items-center gap-2">
                            <StatusBadge tone="neutral">{mapping.kind}</StatusBadge>
                            <StatusBadge
                              tone={toneForStatus(mapping.status)}
                              dot
                            >
                              {mapping.status}
                            </StatusBadge>
                          </div>
                        </div>
                        {mapping.status === "STALE" ? (
                          <p className="mt-4 flex items-start gap-2 border-t border-hairline pt-3 text-[13px] text-muted-foreground">
                            <AlertTriangle
                              className="mt-0.5 size-3.5 shrink-0 text-warning"
                              aria-hidden="true"
                            />
                            The schema changed underneath this mapping. It is no
                            longer used until re-confirmed.
                          </p>
                        ) : null}
                      </Panel>
                    ))}
                  </div>
                )}
              </SectionTransition>
            </TabsPrimitive.Content>

            {/* ---------------------------------------------- recurring questions */}
            <TabsPrimitive.Content value="questions" className="outline-none">
              <SectionTransition>
                <SectionHeader
                  title="Recurring questions"
                  description="Analytical shapes that have been asked more than once. Repeated success here is what proposes a candidate."
                />
                {loading ? (
                  <SkeletonList rows={1} />
                ) : clusters.length === 0 ? (
                  <EmptyState
                    icon={Repeat}
                    title="No recurring patterns yet"
                    description="They appear once the same analytical shape is asked more than once."
                  />
                ) : (
                  <div className="space-y-4">
                    {clusters.map((cluster) => (
                      <Panel interactive key={cluster.id} className="p-5">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <p className="min-w-0 text-[14px] font-medium text-foreground">
                            {cluster.canonicalSummary}
                          </p>
                          <StatusBadge
                            tone={
                              cluster.successfulCount === cluster.occurrenceCount
                                ? "positive"
                                : "neutral"
                            }
                          >
                            {cluster.successfulCount}/{cluster.occurrenceCount}{" "}
                            answered
                          </StatusBadge>
                        </div>
                        <p className="mt-2 truncate font-mono text-[12px] text-muted-foreground">
                          {cluster.structuralFingerprint}
                        </p>
                      </Panel>
                    ))}
                  </div>
                )}
              </SectionTransition>
            </TabsPrimitive.Content>

            {/* ---------------------------------------------------- candidates */}
            <TabsPrimitive.Content value="candidates" className="outline-none">
              <SectionTransition>
                <SectionHeader
                  title="Candidates"
                  count={pendingCandidates.length}
                  description="Metrics, rules and dimensions the system has proposed from patterns that repeatedly succeeded. Nothing here is used to answer a question until a person approves it."
                />
                {loading ? (
                  <SkeletonList rows={4} />
                ) : candidates.length === 0 ? (
                  <EmptyState
                    icon={Lightbulb}
                    title="No knowledge candidates"
                    description="They are proposed from recurring patterns that repeatedly succeeded."
                  />
                ) : (
                  <div className="space-y-4">
                    {candidates.map((candidate) => (
                      <CandidateCard
                        key={candidate.id}
                        candidate={candidate}
                        busy={busyId === candidate.id}
                        onApprove={() => void review(candidate.id, "approve")}
                        onReject={() => void review(candidate.id, "reject")}
                      />
                    ))}
                  </div>
                )}
              </SectionTransition>
            </TabsPrimitive.Content>

            {/* ---------------------------------------------- certified metrics */}
            <TabsPrimitive.Content value="metrics" className="outline-none">
              <SectionTransition>
                <SectionHeader
                  title="Certified metrics"
                  description="Definitions the system is allowed to answer from directly, with the business meaning a reviewer signed off."
                />
                {loading ? (
                  <SkeletonList rows={3} />
                ) : metrics.length === 0 ? (
                  <EmptyState
                    icon={Gauge}
                    title="No certified metrics yet"
                    description="Approving a metric candidate certifies it for this database."
                  />
                ) : (
                  <div className="space-y-4">
                    {metrics.map((metric) => (
                      <Panel interactive key={metric.metricKey} className="p-5">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <div className="min-w-0">
                            <h3 className="text-[15px] font-medium text-foreground">
                              {metric.displayName}
                            </h3>
                            <p className="mt-1 font-mono text-[12px] text-muted-foreground">
                              {metric.metricKey} · v{metric.version}
                            </p>
                          </div>
                          <StatusBadge tone={toneForStatus(metric.status)} dot>
                            {metric.status}
                          </StatusBadge>
                        </div>
                        <p className="measure mt-3 text-[13px] leading-relaxed text-muted-foreground">
                          {metric.businessMeaning || metric.description}
                        </p>
                        <dl className="mt-4 border-t border-hairline pt-3">
                          {metric.grain !== null ? (
                            <DetailRow label="Grain" value={metric.grain} />
                          ) : null}
                          {metric.dimensions.length > 0 ? (
                            <DetailRow
                              label="Dimensions"
                              value={metric.dimensions.join(", ")}
                            />
                          ) : null}
                          {metric.semanticExpression !== null ? (
                            <DetailRow
                              label="Expression"
                              value={metric.semanticExpression}
                              mono
                            />
                          ) : null}
                          {metric.approvedBy !== null ? (
                            <DetailRow
                              label="Approved by"
                              value={metric.approvedBy}
                            />
                          ) : null}
                        </dl>
                      </Panel>
                    ))}
                  </div>
                )}
              </SectionTransition>
            </TabsPrimitive.Content>

            {/* ---------------------------------------------- approved examples */}
            <TabsPrimitive.Content value="examples" className="outline-none">
              <SectionTransition>
                <SectionHeader
                  title="Approved examples"
                  description="Questions whose plan a reviewer approved. They are shown to the planner as precedent for how this database is meant to be queried."
                />
                {loading ? (
                  <SkeletonList rows={1} />
                ) : examples.length === 0 ? (
                  <EmptyState
                    icon={FileCode2}
                    title="No approved examples yet"
                    description="Approving an example teaches the planner how a question of that shape should be answered here."
                  />
                ) : (
                  <div className="space-y-4">
                    {examples.map((example) => (
                      <Panel interactive key={example.id} className="p-5">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <p className="min-w-0 text-[14px] font-medium text-foreground">
                            {example.question}
                          </p>
                          <StatusBadge tone={toneForStatus(example.status)}>
                            {example.status}
                          </StatusBadge>
                        </div>
                        {example.semanticPlan !== "" ? (
                          <p className="measure mt-2 text-[13px] leading-relaxed text-muted-foreground">
                            {example.semanticPlan}
                          </p>
                        ) : null}
                        {/* SQL is deliberately not shown: it describes tables and
                            columns beyond what listing an example requires. */}
                      </Panel>
                    ))}
                  </div>
                )}
              </SectionTransition>
            </TabsPrimitive.Content>

            {/* ---------------------------------------------------- evaluations */}
            <TabsPrimitive.Content value="evaluations" className="outline-none">
              <SectionTransition>
                <SectionHeader
                  title="Evaluations"
                  description="Questions with a known answer, and whether they still answer correctly. A regression is the only thing on this page worth interrupting a day for."
                />
                <EvaluationsPanel dataSourceId={dataSourceId} />
              </SectionTransition>
            </TabsPrimitive.Content>

            {/* -------------------------------------------------- data quality */}
            <TabsPrimitive.Content value="quality" className="outline-none">
              <SectionTransition>
                <SectionHeader
                  title="Data quality"
                  description="What a reviewer has asserted about the data itself. The system already checks that its SQL is correct; this is the other half."
                />
                <QualityPanel dataSourceId={dataSourceId} />
              </SectionTransition>
            </TabsPrimitive.Content>

            {/* --------------------------------------------- time intelligence */}
            <TabsPrimitive.Content value="time" className="outline-none">
              <SectionTransition>
                <SectionHeader
                  title="Time intelligence"
                  description="This database's calendar, and the columns that carry time. Confirming it is what lets fiscal periods be answered rather than declined."
                />
                <TimePanel dataSourceId={dataSourceId} />
              </SectionTransition>
            </TabsPrimitive.Content>
          </div>
        </TabsPrimitive.Root>
      </motion.section>
    </MotionConfig>
  );
}

const FIELD_CLASS =
  "mt-1.5 h-8 w-full rounded-lg border border-border bg-background px-2.5 text-[13px] outline-none transition-colors focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50";

function Field({
  htmlFor,
  label,
  children,
}: {
  htmlFor: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={htmlFor} className="text-[12.5px] font-medium text-muted-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}

/** Three card outlines, so arriving data replaces something the same shape. */
function SkeletonList({ rows = 2 }: { rows?: number }) {
  return (
    <div className="space-y-4">
      {[0, 1, 2].map((index) => (
        <CardSkeleton key={index} rows={rows} />
      ))}
    </div>
  );
}
