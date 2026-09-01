"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  Database,
  FileCode2,
  Gauge,
  Layers,
  Lightbulb,
  RefreshCw,
  Repeat,
  Plus,
  Shapes,
  Sparkles,
  X,
} from "lucide-react";

import { EvaluationsPanel } from "@/components/knowledge/evaluations-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  DEFAULT_DATA_SOURCE,
  DEFAULT_DATA_SOURCE_ID,
  type DataSourceSummary,
} from "@/lib/datasources/datasources";
import {
  fetchCandidates,
  fetchCertifiedMetrics,
  fetchClusters,
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
 * Read-mostly by design. The only mutation is candidate review, and that calls
 * the backend, which re-validates before certifying — the UI never changes a
 * status on its own.
 */
export function KnowledgeConsole({
  dataSourceId = DEFAULT_DATA_SOURCE_ID,
  dataSources = [DEFAULT_DATA_SOURCE],
  onDataSourcesChanged,
}: KnowledgeConsoleProps) {
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
    }
  }, [dataSourceId]);

  useEffect(() => {
    void load();
  }, [load]);

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

  const source =
    dataSources.find((candidate) => candidate.id === dataSourceId) ??
    DEFAULT_DATA_SOURCE;

  if (denied) {
    return (
      <section className="mx-auto max-w-2xl px-6 py-16 text-center">
        <AlertTriangle
          className="mx-auto size-8 text-muted-foreground"
          aria-hidden="true"
        />
        <h1 className="mt-4 text-lg font-semibold">Review authority required</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Reviewing semantics and certifying metrics is separate from analytics
          access. Ask an administrator to grant knowledge review.
        </p>
      </section>
    );
  }

  return (
    <section className="mx-auto w-full max-w-5xl px-6 py-10">
      <header className="mb-6">
        <h1 className="text-xl font-semibold tracking-tight">Knowledge</h1>
        <p className="mt-1 flex items-center gap-1.5 text-sm text-muted-foreground">
          <Database className="size-3.5" aria-hidden="true" />
          {source.name}
          <span aria-hidden="true">·</span>
          <span>{source.databaseType}</span>
        </p>
      </header>

      {notice !== null ? (
        <p
          role="status"
          className="mb-4 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm"
        >
          {notice}
        </p>
      ) : null}

      <Tabs defaultValue="sources">
        <TabsList className="flex-wrap">
          <TabsTrigger value="sources">Data sources</TabsTrigger>
          <TabsTrigger value="review">
            Schema review{proposals.length > 0 ? ` (${proposals.length})` : ""}
          </TabsTrigger>
          <TabsTrigger value="confirmed">Confirmed semantics</TabsTrigger>
          <TabsTrigger value="questions">Recurring questions</TabsTrigger>
          <TabsTrigger value="candidates">Candidates</TabsTrigger>
          <TabsTrigger value="metrics">Certified metrics</TabsTrigger>
          <TabsTrigger value="examples">Approved examples</TabsTrigger>
          <TabsTrigger value="evaluations">Evaluations</TabsTrigger>
        </TabsList>

        {/* --------------------------------------------------- data sources */}
        <TabsContent value="sources" className="mt-4 space-y-3">
          <div className="flex justify-end">
            <Button
              size="sm"
              variant={adding ? "ghost" : "outline"}
              onClick={() => setAdding((current) => !current)}
            >
              <Plus className="size-3.5" aria-hidden="true" />
              {adding ? "Cancel" : "Add data source"}
            </Button>
          </div>

          {adding ? (
            <form
              aria-label="Add data source"
              className="space-y-3 rounded-lg border border-border p-4"
              onSubmit={(event) => {
                event.preventDefault();
                void register();
              }}
            >
              <div className="grid gap-3 sm:grid-cols-3">
                <div>
                  <label
                    htmlFor="ds-name"
                    className="text-xs text-muted-foreground"
                  >
                    Name
                  </label>
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
                    className="mt-1 h-8 w-full rounded-md border border-border bg-background px-2 text-sm"
                  />
                </div>
                <div>
                  <label
                    htmlFor="ds-type"
                    className="text-xs text-muted-foreground"
                  >
                    Database type
                  </label>
                  <select
                    id="ds-type"
                    value={form.databaseType}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        databaseType: event.target.value,
                      }))
                    }
                    className="mt-1 h-8 w-full rounded-md border border-border bg-background px-2 text-sm"
                  >
                    <option value="postgres">postgres</option>
                  </select>
                </div>
                <div>
                  <label
                    htmlFor="ds-connection"
                    className="text-xs text-muted-foreground"
                  >
                    Connection
                  </label>
                  {/* A choice, never free text: the server decides which
                      references exist, so no DSN or password can be typed. */}
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
                    className="mt-1 h-8 w-full rounded-md border border-border bg-background px-2 text-sm"
                  >
                    {connectionRefs.map((ref) => (
                      <option key={ref} value={ref}>
                        {ref}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                Connections are configured on the server. Credentials are never
                entered here and are never sent from the browser.
              </p>
              <Button
                type="submit"
                size="sm"
                disabled={busyId === "register" || connectionRefs.length === 0}
              >
                Register
              </Button>
            </form>
          ) : null}

          {dataSources.map((entry) => (
            <article
              key={entry.id}
              className="rounded-lg border border-border p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="font-medium">{entry.name}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {entry.databaseType} · reference{" "}
                    <code className="rounded bg-muted px-1 py-0.5 text-xs">
                      {entry.connectionRef}
                    </code>
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge variant="secondary">{entry.status}</Badge>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busyId !== null}
                    onClick={() => void scan()}
                  >
                    <RefreshCw className="size-3.5" aria-hidden="true" />
                    {entry.lastScannedAt === null ? "Scan" : "Rescan"}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busyId !== null}
                    onClick={() => void reindex(entry.id)}
                  >
                    <Sparkles className="size-3.5" aria-hidden="true" />
                    Reindex semantic search
                  </Button>
                </div>
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                <Stat label="Certified metrics" value={entry.certifiedMetricCount} />
                <Stat label="Confirmed entities" value={entry.confirmedEntityCount} />
                <Stat label="Awaiting review" value={entry.proposedEntityCount} />
                <Stat label="Recurring patterns" value={entry.recurringClusterCount} />
              </dl>
            </article>
          ))}
        </TabsContent>

        {/* -------------------------------------------------- schema review */}
        <TabsContent value="review" className="mt-4 space-y-3">
          <Empty
            when={proposals.length === 0}
            icon={Shapes}
            message="No proposals awaiting review. Scan a data source to discover what its tables mean."
          />
          {proposals.map((proposal) => (
            <article
              key={proposal.id}
              className="rounded-lg border border-border p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-mono text-xs text-muted-foreground">
                    {proposal.physical}
                  </p>
                  <h2 className="mt-1 font-medium">
                    {proposal.proposedConcept}
                  </h2>
                  {proposal.detail !== "" ? (
                    <p className="mt-0.5 text-sm text-muted-foreground">
                      {proposal.detail}
                    </p>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge variant="outline">{proposal.kind}</Badge>
                  {proposal.confidence !== null ? (
                    <Badge variant="secondary">
                      {Math.round(proposal.confidence * 100)}%
                    </Badge>
                  ) : null}
                </div>
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2">
                <label className="sr-only" htmlFor={`edit-${proposal.id}`}>
                  Corrected meaning for {proposal.physical}
                </label>
                <input
                  id={`edit-${proposal.id}`}
                  value={editing[proposal.id] ?? ""}
                  placeholder={proposal.proposedConcept}
                  onChange={(event) =>
                    setEditing((current) => ({
                      ...current,
                      [proposal.id]: event.target.value,
                    }))
                  }
                  className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-sm"
                />
                <Button
                  size="sm"
                  disabled={busyId === proposal.id}
                  onClick={() => void reviewMapping(proposal.id, "approve")}
                >
                  <Check className="size-3.5" aria-hidden="true" />
                  {editing[proposal.id]?.trim() ? "Save & approve" : "Approve"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busyId === proposal.id}
                  onClick={() => void reviewMapping(proposal.id, "reject")}
                >
                  <X className="size-3.5" aria-hidden="true" />
                  Reject
                </Button>
              </div>
            </article>
          ))}
        </TabsContent>

        {/* --------------------------------------------- confirmed semantics */}
        <TabsContent value="confirmed" className="mt-4 space-y-3">
          <Empty
            when={confirmed.length === 0}
            icon={Layers}
            message="Nothing confirmed yet. Approved mappings appear here and drive how questions are understood."
          />
          {confirmed.map((mapping) => (
            <article
              key={mapping.id}
              className="rounded-lg border border-border p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="font-medium">{mapping.proposedConcept}</h2>
                  <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                    {mapping.physical}
                  </p>
                  {mapping.detail !== "" ? (
                    <p className="mt-1 text-sm text-muted-foreground">
                      {mapping.detail}
                    </p>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge variant="outline">{mapping.kind}</Badge>
                  <Badge
                    variant={
                      mapping.status === "STALE" ? "destructive" : "default"
                    }
                  >
                    {mapping.status}
                  </Badge>
                </div>
              </div>
              {mapping.status === "STALE" ? (
                <p className="mt-3 flex items-center gap-1.5 text-sm text-muted-foreground">
                  <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
                  The schema changed underneath this mapping. It is no longer used
                  until re-confirmed.
                </p>
              ) : null}
            </article>
          ))}
        </TabsContent>

        {/* ---------------------------------------------- recurring questions */}
        <TabsContent value="questions" className="mt-4 space-y-3">
          <Empty
            when={clusters.length === 0}
            icon={Repeat}
            message="No recurring patterns yet. They appear once the same analytical shape is asked more than once."
          />
          {clusters.map((cluster) => (
            <article
              key={cluster.id}
              className="rounded-lg border border-border p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <p className="min-w-0 font-medium">{cluster.canonicalSummary}</p>
                <Badge variant="secondary">
                  {cluster.successfulCount}/{cluster.occurrenceCount} answered
                </Badge>
              </div>
              <p className="mt-2 truncate font-mono text-xs text-muted-foreground">
                {cluster.structuralFingerprint}
              </p>
            </article>
          ))}
        </TabsContent>

        {/* ---------------------------------------------------- candidates */}
        <TabsContent value="candidates" className="mt-4 space-y-3">
          <Empty
            when={candidates.length === 0}
            icon={Lightbulb}
            message="No knowledge candidates. They are proposed from recurring patterns that repeatedly succeeded."
          />
          {candidates.map((candidate) => (
            <article
              key={candidate.id}
              className="rounded-lg border border-border p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="font-medium">{candidate.displayName}</h2>
                  <p className="mt-0.5 text-sm text-muted-foreground">
                    {candidate.description || "Suggested from a recurring pattern."}
                  </p>
                </div>
                <Badge
                  variant={
                    candidate.status === "APPROVED" ? "default" : "secondary"
                  }
                >
                  {candidate.status}
                </Badge>
              </div>

              <dl className="mt-3 space-y-1 text-sm">
                <Row label="Type" value={candidate.candidateType} />
                <Row
                  label="Observed"
                  value={`${candidate.evidenceCount} times · ${candidate.successfulEvidenceCount} successful`}
                />
                {candidate.expression !== null ? (
                  <Row label="Calculation" value={candidate.expression} mono />
                ) : null}
                {candidate.grain !== null ? (
                  <Row label="Grain" value={candidate.grain} />
                ) : null}
                {candidate.dependencies.length > 0 ? (
                  <Row
                    label="Depends on"
                    value={candidate.dependencies.join(", ")}
                  />
                ) : null}
                {candidate.rejectionReason !== null ? (
                  <Row label="Rejected because" value={candidate.rejectionReason} />
                ) : null}
              </dl>

              {candidate.status === "PROPOSED" ? (
                <div className="mt-4 flex gap-2">
                  <Button
                    size="sm"
                    disabled={busyId === candidate.id}
                    onClick={() => void review(candidate.id, "approve")}
                  >
                    <Check className="size-3.5" aria-hidden="true" />
                    Approve
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busyId === candidate.id}
                    onClick={() => void review(candidate.id, "reject")}
                  >
                    <X className="size-3.5" aria-hidden="true" />
                    Reject
                  </Button>
                </div>
              ) : null}
            </article>
          ))}
        </TabsContent>

        {/* ---------------------------------------------- certified metrics */}
        <TabsContent value="metrics" className="mt-4 space-y-3">
          <Empty
            when={metrics.length === 0}
            icon={Gauge}
            message="No certified metrics for this database yet."
          />
          {metrics.map((metric) => (
            <article
              key={metric.metricKey}
              className="rounded-lg border border-border p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="font-medium">{metric.displayName}</h2>
                  <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                    {metric.metricKey} · v{metric.version}
                  </p>
                </div>
                <Badge>{metric.status}</Badge>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                {metric.businessMeaning || metric.description}
              </p>
              <dl className="mt-3 space-y-1 text-sm">
                {metric.grain !== null ? (
                  <Row label="Grain" value={metric.grain} />
                ) : null}
                {metric.dimensions.length > 0 ? (
                  <Row label="Dimensions" value={metric.dimensions.join(", ")} />
                ) : null}
                {metric.semanticExpression !== null ? (
                  <Row label="Expression" value={metric.semanticExpression} mono />
                ) : null}
                {metric.approvedBy !== null ? (
                  <Row label="Approved by" value={metric.approvedBy} />
                ) : null}
              </dl>
            </article>
          ))}
        </TabsContent>

        {/* ---------------------------------------------- approved examples */}
        <TabsContent value="examples" className="mt-4 space-y-3">
          <Empty
            when={examples.length === 0}
            icon={FileCode2}
            message="No approved query examples yet."
          />
          {examples.map((example) => (
            <article
              key={example.id}
              className="rounded-lg border border-border p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <p className="min-w-0 font-medium">{example.question}</p>
                <Badge variant="secondary">{example.status}</Badge>
              </div>
              {example.semanticPlan !== "" ? (
                <p className="mt-2 text-sm text-muted-foreground">
                  {example.semanticPlan}
                </p>
              ) : null}
              {/* SQL is deliberately not shown: it describes tables and columns
                  beyond what listing an example requires. */}
            </article>
          ))}
        </TabsContent>

        {/* ---------------------------------------------------- evaluations */}
        <TabsContent value="evaluations" className="mt-4">
          <EvaluationsPanel dataSourceId={dataSourceId} />
        </TabsContent>
      </Tabs>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-lg font-medium tabular-nums">{value}</dd>
    </div>
  );
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex gap-2">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className={mono ? "min-w-0 font-mono text-xs leading-5" : "min-w-0"}>
        {value}
      </dd>
    </div>
  );
}

function Empty({
  when,
  icon: Icon,
  message,
}: {
  when: boolean;
  icon: typeof Layers;
  message: string;
}) {
  if (!when) return null;
  return (
    <p className="flex items-center gap-2 rounded-lg border border-dashed border-border px-4 py-8 text-sm text-muted-foreground">
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      {message}
    </p>
  );
}
