import { Workspace } from "@/components/workspace";

/**
 * The workspace route stays a Server Component; only the interactive shell
 * below it ships as client JavaScript. There is no server-side data to fetch —
 * every analytical request is user-initiated and authenticated per request.
 */
export default function Page() {
  return <Workspace />;
}
