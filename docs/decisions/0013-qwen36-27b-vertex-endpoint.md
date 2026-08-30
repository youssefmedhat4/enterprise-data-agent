# ADR 0013: Qwen3.6-27B on a Vertex AI Model Garden Endpoint

Date: 2026-08-30

## Status

Accepted. Supersedes the Gemini model routing described in ADR 0009 and
`docs/backend-v1.md`; Gemini remains configurable but is no longer the active model.

## Context

Both logical model aliases — `analytics-general` and `sql-reasoner` — resolved to
`vertex_ai/gemini-2.5-flash`, a managed third-party model. The goal of this milestone was
to run a specific open-weight model, `Qwen/Qwen3.6-27B`, inside the project's own Google
Cloud account, without changing anything else about the architecture.

AGENTS.md already anticipated this: application code addresses logical aliases, never
physical models, so moving to self-hosted inference should be a configuration change
rather than a code change. This ADR records whether that held.

## Decision

`Qwen/Qwen3.6-27B` is deployed through Vertex AI Model Garden's supported open-model path,
served by Google's prebuilt vLLM container, and reached through the existing
`LLMGateway` → `LiteLLMGateway` → LiteLLM chain.

```text
LangGraph
   -> LLMGateway (unchanged)
      -> LiteLLMGateway (unchanged)
         -> vertex_ai/openai/<endpoint id>
            -> Vertex AI endpoint, prebuilt vLLM container
               -> Qwen3.6-27B, BF16
```

### Serving configuration

| | |
| --- | --- |
| Publisher model | `publishers/qwen/models/qwen3-6@qwen3.6-27b` |
| Region | `us-central1` |
| Machine type | `g4-standard-48` |
| Accelerator | `NVIDIA_RTX_PRO_6000` x 1 (96 GB) |
| Container | `pytorch-vllm-serve:20260226_0916_RC01` (Google prebuilt) |
| Precision | BF16, unquantized official weights |

Google publishes exactly two supported configurations for this model. The other,
`a3-highgpu-1g` with an H100 80GB, was not an option: **H100 serving quota on this project
is 0 in every region**, while RTX PRO 6000 quota is 2 in `us-central1`. The choice was
therefore forced rather than preferred.

The card is not oversized. 27B parameters at BF16 is 54 GB of weights, which rules out
L4 (24 GB) entirely and leaves 42 GB on a 96 GB card for KV cache and activations.

### Why a shared endpoint, not a dedicated one

Model Garden defaults to `--use-dedicated-endpoint`, which serves from
`*.prediction.vertexai.goog`. That domain is **TLS-intercepted on the deployment network by
a Fortinet appliance whose CA the machine does not trust**, so a dedicated endpoint deploys
successfully and is then unreachable — verified directly by inspecting the certificate
chain. `*-aiplatform.googleapis.com` presents a genuine Google Trust Services certificate
and is reachable.

The endpoint is therefore deployed **without** the dedicated flag and served over the
shared regional domain. This is a deliberate constraint of the network, not a preference.

### Authentication

LiteLLM's `vertex_ai/openai/` path authenticates with Application Default Credentials and
refreshes them itself. No bearer token, service-account key, or Hugging Face token is
stored in `.env`, in the repository, or in any config file. The endpoint is IAM-protected
and is not a public unauthenticated API.

### Cloud data guard is unchanged

`model_provider()` returns `vertex_ai` for this model string, so the endpoint is already
classified as a cloud provider by the existing check and still requires
`ALLOW_CLOUD_DATABASE_DATA=1` before database-derived content is sent to it. Self-hosting
the weights inside the project does not exempt it: the data still leaves the process.

### No silent fallback

Gemini is not a fallback. If the Qwen endpoint is undeployed or unreachable the request
fails with a typed `LLMGatewayError`, exactly as before. Restoring Gemini is an explicit
configuration change, never an automatic one.

## Consequences

- **Cost changes shape.** Gemini billed per token; this endpoint is a provisioned GPU that
  bills continuously — roughly $4.50/hour, about $108/day — whether or not a request is
  made. Undeploying is the only way to stop it, and is the expected steady state between
  working sessions.
- **Availability changes shape.** A managed Google model is effectively always available;
  a self-hosted endpoint is available only while deployed, which is why the typed
  unavailable error matters more now than it did before.
- No application code changed to switch models. `LLM_MODEL_ANALYTICS_GENERAL` and
  `LLM_MODEL_SQL_REASONER` moved to the endpoint, and `VERTEXAI_LOCATION` moved to
  `us-central1`. The alias indirection did what it was built to do.

## Operations

Checking, stopping, and restarting are documented in `docs/backend-v1.md`. The distinction
that matters: **undeploying the model stops GPU billing** while keeping the endpoint and
model registry entry, so redeploying is cheap. Deleting the endpoint and model resources is
permanent and is only needed when abandoning the deployment entirely.

## Structured output

Qwen3.6 is a reasoning model: by default it emits a thinking trace before the answer, which
is not valid JSON and broke every structured response. Passing
`chat_template_kwargs: {"enable_thinking": false}` turns that off, after which
`response_format` with a JSON schema returns clean, schema-valid output. The application
sets this automatically for any `vertex_ai/openai/` model and never for a managed Vertex
model, which would reject it.

**Tool calling is not available on this endpoint.** Google's prebuilt vLLM container is not
started with `--enable-auto-tool-choice` and `--tool-call-parser`, so `tool_choice=auto`
returns HTTP 400. `response_format` is therefore the mode of record, configured through the
existing per-alias `structured_output_modes_by_alias` setting (its default) rather than by
weakening any schema.

One prompt change was needed. Qwen fills in every optional field rather than omitting the
ones it has no reason to set, and it initially set `series` to the same column as `x`, which
`ChartSpec` correctly rejects as meaningless. The answer prompt now states when `series`
applies and that optional fields should be left unset — guidance that is model-neutral and
makes the contract clearer for any model, rather than a Qwen-specific workaround. The
validation constraint was not relaxed.

## Risks

- A single replica in a single region is not a high-availability design. That remains
  deliberately deferred, consistent with the Kubernetes/HA decisions already postponed.
- The endpoint depends on the shared-domain workaround above. If the network's TLS
  inspection policy changes, revisit whether a dedicated endpoint is preferable.
- Structured output depends on what the vLLM container supports rather than on Gemini's
  native behaviour; the mode actually used is recorded in `docs/backend-v1.md` and enforced
  per alias through the existing `structured_output_modes_by_alias` setting.
