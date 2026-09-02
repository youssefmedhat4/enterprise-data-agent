"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { DataSourceSelector } from "@/components/conversation/datasource-selector";
import { KnowledgeConsole } from "@/components/knowledge/knowledge-console";
import {
  DEFAULT_DATA_SOURCE,
  DEFAULT_DATA_SOURCE_ID,
  parseDataSources,
  type DataSourceSummary,
} from "@/lib/datasources/datasources";

/** Shared with the workspace, so the database you were querying is the one you review. */
const DATA_SOURCE_STORAGE_KEY = "eda.data-source:v1";

/**
 * Reviewer surface for learned knowledge.
 *
 * The console does the work; this page only decides *which database* is being
 * reviewed and gives a way back. Both matter: knowledge is scoped per
 * datasource, so a console fixed to the default one shows an empty queue while
 * the proposals sit unreviewed on another database — which reads as "nothing to
 * review" rather than "you are looking at the wrong place".
 *
 * Every route behind the console enforces review authority server-side; nothing
 * privileged is decided here.
 */
export default function KnowledgePage() {
  const [dataSources, setDataSources] = useState<DataSourceSummary[]>([
    DEFAULT_DATA_SOURCE,
  ]);
  const [dataSourceId, setDataSourceId] = useState<string>(DEFAULT_DATA_SOURCE_ID);

  const loadSources = useCallback(async () => {
    try {
      const response = await fetch("/api/backend/knowledge/data-sources", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const parsed = parseDataSources(await response.json());
      if (parsed.length > 0) setDataSources(parsed);
    } catch {
      // Unavailable or unauthorized: the console reports it in context.
    }
  }, []);

  // Read after mount, never during render: the page is prerendered, so the
  // first paint has to match the server's. A link from an answer names the
  // datasource it was answered from, which outranks whatever was last selected.
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("dataSource");
    const stored = sessionStorage.getItem(DATA_SOURCE_STORAGE_KEY);
    if (requested !== null) {
      setDataSourceId(requested);
      sessionStorage.setItem(DATA_SOURCE_STORAGE_KEY, requested);
    } else if (stored !== null) {
      setDataSourceId(stored);
    }
    void loadSources();
  }, [loadSources]);

  const selectDataSource = (next: string) => {
    setDataSourceId(next);
    sessionStorage.setItem(DATA_SOURCE_STORAGE_KEY, next);
  };

  return (
    <div className="min-h-dvh bg-background">
      {/* A fixed height, because the section navigation sticks underneath it
          and needs a number to clear. */}
      <header className="sticky top-0 z-20 flex h-14 items-center gap-2.5 border-b border-hairline bg-background/80 px-6 backdrop-blur-md lg:px-10">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 rounded-md text-[13px] text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <ArrowLeft className="size-3.5" aria-hidden="true" />
          Workspace
        </Link>
        <span aria-hidden="true" className="text-muted-foreground/50">
          /
        </span>
        <span className="text-[13px] font-medium text-foreground">Knowledge</span>
        <div className="ms-auto">
          <DataSourceSelector
            value={dataSourceId}
            sources={dataSources}
            onValueChange={selectDataSource}
            disabled={false}
            compact
          />
        </div>
      </header>

      <KnowledgeConsole
        dataSourceId={dataSourceId}
        dataSources={dataSources}
        onDataSourcesChanged={loadSources}
      />
    </div>
  );
}
