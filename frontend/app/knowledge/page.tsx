import { KnowledgeConsole } from "@/components/knowledge/knowledge-console";

/**
 * Reviewer surface for learned knowledge.
 *
 * A Server Component wrapper only: the console fetches through the same-origin
 * proxy per interaction, and every route behind it enforces review authority
 * server-side, so nothing privileged is resolved at render time.
 */
export default function KnowledgePage() {
  return <KnowledgeConsole />;
}
