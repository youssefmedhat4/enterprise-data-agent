/**
 * Presentation for the backend's typed error codes.
 *
 * The backend already sanitises every message, so `message` is safe to display
 * verbatim. What it cannot know is what the *user* should do next — that is what
 * this map adds. Retry is offered only where retrying could plausibly succeed.
 */

export type ErrorTone = "unavailable" | "denied" | "rejected" | "unexpected";

export interface ErrorPresentation {
  title: string;
  guidance: string;
  tone: ErrorTone;
  /** Whether a Try again button should appear, independent of transport retry. */
  allowRetry: boolean;
}

const PRESENTATION: Record<string, ErrorPresentation> = {
  network_unreachable: {
    title: "Can't reach the analytics service",
    guidance:
      "The backend did not respond. Confirm it is running, then try again.",
    tone: "unavailable",
    allowRetry: true,
  },
  request_cancelled: {
    title: "Analysis stopped",
    guidance: "You stopped this analysis before it finished.",
    tone: "rejected",
    allowRetry: true,
  },
  database_unavailable: {
    title: "Analytics database unavailable",
    guidance:
      "The database could not be reached. This is usually temporary — try again shortly.",
    tone: "unavailable",
    allowRetry: true,
  },
  database_configuration_error: {
    title: "Database not safely configured",
    guidance:
      "The service refused to run because its database credentials are not verified read-only. This needs an administrator.",
    tone: "unavailable",
    allowRetry: false,
  },
  database_permission_denied: {
    title: "No access to that data",
    guidance:
      "The analytics role cannot read the data this question needs. Ask for access, or try a different question.",
    tone: "denied",
    allowRetry: false,
  },
  authorization_denied: {
    title: "Not authorised",
    guidance:
      "Your access does not cover the data or metric this question requires. Try a question within your scope.",
    tone: "denied",
    allowRetry: false,
  },
  authorization_unavailable: {
    title: "Policy service unavailable",
    guidance:
      "Access could not be evaluated, so the request was refused rather than guessed. Try again shortly.",
    tone: "unavailable",
    allowRetry: true,
  },
  authentication_failed: {
    title: "Sign-in required",
    guidance: "Your credentials were not accepted. Sign in again to continue.",
    tone: "denied",
    allowRetry: false,
  },
  authentication_unavailable: {
    title: "Identity provider unavailable",
    guidance: "Sign-in could not be verified right now. Try again shortly.",
    tone: "unavailable",
    allowRetry: true,
  },
  checkpoint_unavailable: {
    title: "Conversation memory unavailable",
    guidance:
      "Follow-up context could not be loaded or saved. Starting a new analysis may work.",
    tone: "unavailable",
    allowRetry: true,
  },
  governance_provider_unavailable: {
    title: "Catalog unavailable",
    guidance:
      "Required catalog metadata could not be retrieved. Try again shortly.",
    tone: "unavailable",
    allowRetry: true,
  },
  metric_provider_unavailable: {
    title: "Governed metric service unavailable",
    guidance:
      "The certified metric layer is not responding. Governed figures are never approximated, so nothing was returned.",
    tone: "unavailable",
    allowRetry: true,
  },
  semantic_provider_unavailable: {
    title: "Business context unavailable",
    guidance:
      "The semantic layer could not supply context for this question. Try again shortly.",
    tone: "unavailable",
    allowRetry: true,
  },
  llm_unavailable: {
    title: "Model unavailable",
    guidance:
      "The language model is not responding. This is usually temporary.",
    tone: "unavailable",
    allowRetry: true,
  },
  llm_rate_limited: {
    title: "Model rate limited",
    guidance: "Too many requests right now. Wait a moment, then try again.",
    tone: "unavailable",
    allowRetry: true,
  },
  invalid_structured_model_output: {
    title: "Model returned an unusable response",
    guidance: "The response did not match the expected shape. Try again.",
    tone: "unexpected",
    allowRetry: true,
  },
  query_timeout: {
    title: "Query timed out",
    guidance:
      "The analysis took longer than the configured limit. A narrower question usually completes.",
    tone: "unavailable",
    allowRetry: true,
  },
  result_too_large: {
    title: "Result too large",
    guidance:
      "The answer exceeded the response size limit. Add a filter or ask for a summary instead of full rows.",
    tone: "rejected",
    allowRetry: false,
  },
  unsafe_sql: {
    title: "Query rejected by safety validation",
    guidance:
      "The generated query did not pass read-only validation and was not run. Rephrasing usually resolves it.",
    tone: "rejected",
    allowRetry: false,
  },
  sql_validation_failed: {
    title: "Query failed validation",
    guidance:
      "The generated query could not be validated and was not run. Try rephrasing the question.",
    tone: "rejected",
    allowRetry: false,
  },
  sql_schema_validation_failed: {
    title: "Query did not match the available schema",
    guidance:
      "The question referenced data that is not available to you. Try naming the tables or fields you mean.",
    tone: "rejected",
    allowRetry: false,
  },
  sql_repair_failed: {
    title: "Query could not be repaired",
    guidance:
      "The query still did not fit the available schema after one repair attempt. Try a more specific question.",
    tone: "rejected",
    allowRetry: false,
  },
  query_execution_failed: {
    title: "Query could not be executed",
    guidance:
      "The database rejected the validated query. Try rephrasing the question.",
    tone: "rejected",
    allowRetry: false,
  },
  grounding_failure: {
    title: "Answer could not be verified",
    guidance:
      "The generated answer did not match the query result, so it was withheld rather than shown unverified. Try asking again.",
    tone: "rejected",
    allowRetry: true,
  },
  invalid_metric_query: {
    title: "Unsupported metric request",
    guidance:
      "That combination is not defined for this governed metric. Try different dimensions or filters.",
    tone: "rejected",
    allowRetry: false,
  },
  metric_planning_failure: {
    title: "Metric request could not be planned",
    guidance:
      "The governed request could not be expressed safely. Try asking for one metric at a time.",
    tone: "rejected",
    allowRetry: false,
  },
  router_failure: {
    title: "Question could not be routed",
    guidance:
      "The request could not be directed to a safe execution path. Try rephrasing it.",
    tone: "rejected",
    allowRetry: false,
  },
  invalid_request: {
    title: "Invalid request",
    guidance: "The question could not be accepted in this form.",
    tone: "rejected",
    allowRetry: false,
  },
  internal_unexpected_error: {
    title: "Something went wrong",
    guidance: "The analysis did not complete. Try again.",
    tone: "unexpected",
    allowRetry: true,
  },
};

const FALLBACK: ErrorPresentation = {
  title: "Analysis failed",
  guidance: "The request did not complete. Try again.",
  tone: "unexpected",
  allowRetry: true,
};

export function presentError(code: string): ErrorPresentation {
  return PRESENTATION[code] ?? FALLBACK;
}
