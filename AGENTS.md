\# Enterprise Data Agent



\## Project Status



This is a GREENFIELD project.



This repository contains a completely new implementation of an enterprise conversational data and analytics platform.



Do not copy architecture or code from any older prototype unless explicitly instructed.



Design this system according to the target architecture and engineering principles in this file.



\---



\# Mission



Build a production-grade enterprise conversational analytics platform.



Users should be able to ask natural-language questions about enterprise data and receive:



\* accurate factual answers

\* database results

\* analytical insights

\* comparisons

\* summaries

\* trends

\* structured chart specifications

\* provenance/source information

\* data freshness information where available



The platform must ultimately support:



\* many enterprise users

\* multiple databases

\* multiple business domains

\* enterprise authentication

\* authorization

\* governed business metrics

\* complex Text-to-SQL

\* multilingual English/Arabic questions

\* conversation memory

\* local production LLM inference

\* multiple models

\* scalable GPU inference

\* auditing

\* observability

\* automated evaluation

\* enterprise deployment



The system must be modular, secure, testable, scalable, and maintainable.



\---



\# Fundamental Product Boundary



The platform is READ ONLY.



It is an analytics assistant, not a database administration agent.



Never implement database mutation capabilities such as:



\* INSERT

\* UPDATE

\* DELETE

\* DROP

\* ALTER

\* TRUNCATE

\* CREATE

\* GRANT

\* REVOKE

\* write-capable stored procedures



The LLM must never receive unrestricted database credentials.



Database permissions are the final security boundary.



\---



\# High-Level Request Flow



The target flow is:



User

→ Enterprise UI/API

→ Authentication

→ LangGraph

→ Authorization

→ Intent/query planning

→ Context retrieval

→ Semantic analytics OR complex Text-to-SQL

→ SQL/query validation

→ Database gateway

→ Read-only analytical database

→ Structured query result

→ Grounding/provenance

→ General reasoning model

→ Final answer and optional chart specification



\---



\# Architecture Principles



\## Prefer Existing Open-Source Infrastructure



Do not unnecessarily build infrastructure that mature open-source tools already provide.



Prefer existing tools for:



\* authentication

\* LLM routing

\* model serving

\* database connectivity

\* semantic layers

\* metadata catalogs

\* authorization

\* observability

\* tracing

\* monitoring



Custom code should focus primarily on:



\* LangGraph orchestration

\* company-specific integrations

\* adapters

\* grounding

\* provenance

\* evaluation

\* policies

\* application contracts



\---



\# Loose Coupling



Major components must remain replaceable.



Application logic must not depend directly on a particular:



\* LLM provider

\* model

\* database vendor

\* MCP implementation

\* semantic layer

\* metadata platform

\* observability platform



Use internal interfaces/adapters such as:



\* LLMGateway

\* DatabaseGateway

\* SemanticGateway

\* MetadataGateway

\* AuthorizationService

\* TraceService



\---



\# Backend



Use Python 3.12+ unless compatibility requires otherwise.



Use FastAPI for the backend API.



Use:



\* type hints

\* Pydantic

\* async I/O where appropriate

\* structured logging

\* explicit configuration

\* modular code

\* unit tests

\* integration tests

\* security tests



Avoid giant files and hidden global state.



\---



\# Agent Orchestration



Use LangGraph.



LangGraph owns:



\* workflow control

\* typed request state

\* routing

\* retries

\* controlled tool execution

\* failure handling

\* conversation flow

\* multi-step analytical workflows



Do not build an uncontrolled autonomous agent loop.



Prefer explicit nodes and conditional edges.



\---



\# Agent State



Create typed state.



Potential state fields include:



\* request\_id

\* trace\_id

\* thread\_id

\* user identity

\* question

\* intent

\* authorization decision

\* retrieved metadata

\* semantic context

\* query plan

\* generated SQL

\* validated SQL

\* query result

\* provenance

\* chart specification

\* final answer

\* errors

\* model route



Only add fields when they are needed.



\---



\# Conversation Memory



Use PostgreSQL-backed LangGraph checkpointing in the production architecture.



Do not depend on process-local memory.



Do not continually resend unlimited conversation history.



Maintain useful structured state such as:



\* previous metric

\* previous dimensions

\* current filters

\* date range

\* previous query

\* previous result reference

\* conversation summary

\* recent turns



LangGraph handles application/conversation memory.



It does NOT provide model prefix caching.



\---



\# LLM Gateway



Use LiteLLM.



Application code must use logical model aliases rather than physical provider/model names.



Examples:



\* analytics-general

\* sql-reasoner

\* fast-agent

\* embedding-model



Do not hardcode model names throughout LangGraph nodes.



Example:



development:

analytics-general → approved cloud model



production:

analytics-general → local model through SGLang



Changing model infrastructure must not require rewriting the agent workflow.



\---



\# Development Models



Cloud models may be used during development to accelerate testing.



Only use them with:



\* synthetic data

\* public data

\* explicitly approved non-sensitive data



Never send confidential enterprise information to an unapproved cloud service.



Real company data will later use approved local infrastructure.



\---



\# Production Inference



Target SGLang for production local model serving.



SGLang should eventually provide:



\* local GPU inference

\* RadixAttention

\* prefix/KV caching

\* continuous batching

\* structured generation

\* quantization where appropriate

\* multi-GPU workers

\* multi-node scaling

\* cache-aware routing



At enterprise scale, use an SGLang-aware gateway/router.



\---



\# Prefix Caching Strategy



Stable prompt content should appear before dynamic content.



Preferred order:



1\. stable system instructions

2\. stable security rules

3\. stable tool contracts

4\. stable output schemas

5\. relevant retrieved business context

6\. relevant schema

7\. relevant examples

8\. conversation context

9\. current question



Do not put volatile information unnecessarily near the start of prompts.



Do not dump the entire enterprise schema into every request.



Retrieve only relevant context.



SGLang handles model-level KV/prefix caching.



LangGraph handles application state.



\---



\# Semantic Analytics



We will evaluate two approaches.



\## Candidate A



Cube Core for:



\* governed metrics

\* dimensions

\* joins

\* business analytics

\* pre-aggregations



plus Wren for complex/ad-hoc Text-to-SQL.



\## Candidate B



Wren MDL + Wren Cubes for:



\* semantic analytics

\* Text-to-SQL context



Do not permanently maintain duplicate metric definitions in both systems.



The final choice must be based on testing and maintainability.



\---



\# Business Definitions



The LLM must not invent authoritative business definitions.



Examples:



\* Revenue

\* Headcount

\* Active Employee

\* Payroll

\* Utilization

\* Chargeability

\* Gross Margin

\* Project Margin

\* Active Project



These definitions belong in a governed semantic/business layer.



\---



\# Text-to-SQL



Primary context-engine candidate:



\* Wren



SQL-specialized model candidate:



\* XiYanSQL / XiYanSQL-QwenCoder



Benchmark specialized SQL models against general reasoning models.



Complex SQL generation should use relevant:



\* schema

\* relationships

\* semantic definitions

\* business context

\* known-good examples

\* successful previous queries

\* execution feedback

\* validation errors



Do not rely on sending the entire schema to a general LLM.



\---



\# Database Access



The first development implementation should use an internal DatabaseGateway with a direct PostgreSQL adapter.



Do NOT require MCP infrastructure for the first working vertical slice.



Conceptually:



LangGraph

→ DatabaseGateway

→ PostgreSQL adapter

→ synthetic PostgreSQL



The DatabaseGateway should support responsibilities such as:



\* search\_schema()

\* get\_table\_metadata()

\* explain\_query()

\* execute\_readonly()

\* health\_check()



Later we will evaluate MCP Toolbox for Databases as an enterprise database-access adapter.



LangGraph must not depend directly on PostgreSQL or MCP Toolbox.



\---



\# MCP Toolbox



MCP Toolbox for Databases is a production candidate for standardized enterprise database connectivity.



Its role is database access and connectivity, NOT Text-to-SQL reasoning.



It may eventually provide a common layer for multiple database technologies.



Do not introduce it before the initial direct PostgreSQL vertical slice is working.



\---



\# DBHub



DBHub is not a core dependency of this project.



It may be used later only as:



\* a benchmark baseline

\* debugging tooling

\* temporary development tooling



Do not build the architecture around DBHub.



\---



\# SQL Validation



Use SQLGlot.



Generated SQL must be parsed as an AST.



Do not rely primarily on regular expressions.



Validation must eventually include:



\* exactly one statement

\* read-only operation

\* allowed statement types

\* allowed schemas

\* allowed tables when required

\* prohibited operations

\* unsafe functions where necessary

\* row/result limits

\* query timeout policies



Database credentials must still physically prevent data modification.



SQLGlot is a validation layer, not the final security boundary.



\---



\# Data Access



Prefer:



Operational systems

→ replication / ETL / analytics replica

→ governed views / analytics schemas

→ semantic layer/database gateway

→ AI application



Avoid expensive LLM-generated analytical queries directly against critical transactional systems when an analytical replica is available.



\---



\# Authentication



Use the company's existing enterprise Identity Provider when available.



Keycloak is a candidate if one is required.



Do not build custom password/MFA infrastructure unnecessarily.



Identity must propagate through the complete request chain.



\---



\# Authorization



Use Open Policy Agent (OPA) as the target policy engine.



The LLM must not determine whether users may access data.



Authorization may depend on:



\* identity

\* role

\* department

\* business domain

\* resource

\* data sensitivity

\* requested operation



Platform administrators must not automatically gain access to confidential business data.



Authorization must fail closed.



\---



\# Security Layers



Use defense in depth.



Possible layers:



1\. authentication

2\. OPA authorization

3\. semantic-layer access controls

4\. SQLGlot validation

5\. read-only DB credentials

6\. governed views

7\. row-level security

8\. column-level security

9\. query timeout

10\. row/result limits

11\. audit logging



Never rely on prompts as the primary security mechanism.



\---



\# Metadata and Governance



Target OpenMetadata.



Eventually use metadata such as:



\* tables

\* columns

\* descriptions

\* ownership

\* lineage

\* glossary

\* domains

\* classifications

\* PII/sensitivity metadata

\* freshness

\* quality information



Do not inject the entire metadata catalog into every LLM request.



Retrieve only relevant context.



\---



\# Retrieval



Use PostgreSQL + pgvector when retrieval becomes necessary.



Potential retrieval content:



\* known-good question/SQL pairs

\* table descriptions

\* metric descriptions

\* business aliases

\* Arabic/English aliases

\* entity aliases

\* semantic examples



Do not embed structured enterprise data without a reason.



\---



\# Grounding



All factual/numerical answers derived from databases must be grounded in executed query results.



The final model must not invent unsupported:



\* numbers

\* percentages

\* dates

\* entities

\* rankings

\* statistics



If a numerical claim is unsupported, reject or repair the answer.



\---



\# Provenance



Eventually retain provenance such as:



\* request ID

\* query ID

\* source

\* semantic metric

\* dimensions

\* filters

\* generated SQL

\* validated SQL

\* result fields

\* freshness timestamp

\* model version

\* semantic model version



The API/UI should eventually be able to expose this information.



\---



\# Charts



The LLM should output structured chart specifications.



Do not execute arbitrary LLM-generated Python for visualizations.



A ChartSpec may contain:



\* chart type

\* title

\* x dimension

\* y measure

\* series/grouping

\* sorting

\* formatting



Rendering happens deterministically outside the LLM.



\---



\# AI Observability



Target Langfuse.



Use it eventually for:



\* model traces

\* prompts

\* tool calls

\* SQL generation

\* corrections

\* latency

\* token usage

\* grounding

\* evaluations

\* user feedback



Do not automatically log sensitive enterprise result rows.



\---



\# Platform Observability



Target:



\* OpenTelemetry

\* Prometheus

\* Grafana



Every request should have:



\* request\_id

\* trace\_id



Eventually record:



\* node latency

\* DB latency

\* model latency

\* TTFT

\* throughput

\* tokens

\* errors

\* cache hit rate

\* GPU utilization



Observability must not become a hard dependency that prevents the application from running.



\---



\# Secrets



Development may use `.env`.



Never commit `.env`.



Production target:



\* OpenBao

\* or the company's approved secrets manager



Never commit:



\* API keys

\* tokens

\* database passwords

\* certificates

\* production credentials



\---



\# Evaluation



Architecture decisions must be evidence-based.



Build an evaluation suite containing categories such as:



\* simple lookup

\* aggregation

\* joins

\* nested queries

\* CTEs

\* window functions

\* temporal reasoning

\* comparative analysis

\* difficult analytical questions

\* ambiguity

\* follow-ups

\* English

\* Arabic

\* mixed Arabic/English

\* authorization

\* security/adversarial cases



Each evaluation case may contain:



\* question

\* expected intent

\* relevant tables

\* expected metric

\* expected result

\* tolerance

\* expected access decision

\* language

\* difficulty



Metrics should eventually include:



\* execution accuracy

\* answer accuracy

\* SQL validity

\* grounding accuracy

\* latency

\* retry count

\* structured-output reliability



\---



\# Planned Benchmarks



Eventually benchmark:



1\. direct PostgreSQL + general model baseline

2\. MCP Toolbox + general model

3\. Wren + general model

4\. Wren + XiYanSQL

5\. Cube + Wren

6\. Wren MDL + Wren Cubes



Use the SAME evaluation dataset.



Do not choose tools based only on popularity or claimed benchmarks.



\---



\# Repository Structure



The project should evolve toward:



enterprise-data-agent/

├── app/

│   ├── api/

│   ├── agent/

│   │   ├── graph.py

│   │   ├── state.py

│   │   ├── nodes/

│   │   └── prompts/

│   ├── contracts/

│   ├── llm/

│   ├── data/

│   ├── semantic/

│   ├── metadata/

│   ├── security/

│   ├── observability/

│   └── config.py

├── tests/

│   ├── unit/

│   ├── integration/

│   ├── evals/

│   └── security/

├── semantic/

│   ├── cube/

│   └── wren/

├── infra/

│   ├── compose/

│   ├── kubernetes/

│   └── observability/

├── scripts/

├── docs/

│   ├── architecture/

│   └── decisions/

├── AGENTS.md

├── pyproject.toml

├── .env.example

├── .gitignore

└── README.md



This is a target structure.



Do not create empty folders/modules simply because they appear here.



Only create components when required by the current implementation stage.



\---



\# Development Roadmap



\## Build 01 — Foundation



Create:



\* Python project configuration

\* package structure

\* configuration foundation

\* testing foundation

\* Ruff

\* mypy

\* pytest

\* `.env.example`

\* README

\* basic FastAPI application

\* health endpoint



Do not integrate heavy infrastructure yet.



\---



\## Build 02 — LLM Abstraction



Implement:



\* LLMGateway

\* LiteLLM adapter

\* logical model aliases

\* structured outputs

\* deterministic fake/test LLM

\* tests



A cloud model may be used when an approved API key is configured.



\---



\## Build 03 — LangGraph Skeleton



Implement:



\* typed AgentState

\* minimal LangGraph graph

\* deterministic nodes

\* request lifecycle

\* tests



Do not implement SQL yet.



\---



\## Build 04 — Synthetic PostgreSQL



Create a realistic synthetic enterprise database.



Suggested domains:



\* departments

\* employees

\* payroll

\* projects

\* employee\_project\_assignments

\* customers

\* invoices

\* invoice\_lines

\* project\_costs



Include:



\* primary keys

\* foreign keys

\* historical data

\* monetary values

\* statuses

\* many-to-many relationships

\* English and Arabic names/data



Never use real company data.



\---



\## Build 05 — DatabaseGateway



Create a replaceable internal database interface.



Implement direct PostgreSQL access first.



Use a read-only application database user.



\---



\## Build 06 — SQL Safety



Implement SQLGlot AST validation.



Add tests covering mutation and SQL injection-like attempts.



\---



\## Build 07 — First Vertical Slice



Implement:



Natural-language question

→ relevant schema

→ SQL generation

→ SQL validation

→ read-only execution

→ structured results

→ grounding

→ final answer

→ optional ChartSpec

→ provenance



The first important test question should be:



"Show each department, its number of employees, total salary, average salary, and highest paid employee, ordered by total payroll."



Do not consider this build complete until the automated integration test works.



\---



\## Build 08 — Conversation Persistence



Add PostgreSQL-backed LangGraph checkpointing.



Support thread IDs and structured analytical context.



\---



\## Build 09 — Evaluation Harness



Create automated analytics/Text-to-SQL evaluation infrastructure.



This must exist before comparing large architectural alternatives.



\---



\## Build 10 — MCP Toolbox Experiment



Implement MCP Toolbox through the DatabaseGateway interface.



Benchmark against the direct PostgreSQL adapter.



Do not change LangGraph simply because the DB adapter changed.



\---



\## Build 11 — Wren Experiment



Integrate Wren through an adapter.



Benchmark against the baseline.



\---



\## Build 12 — Cube Experiment



Integrate Cube for governed metrics.



Compare:



Cube + Wren



versus:



Wren semantic/MDL approach



Do not maintain two permanent semantic layers.



Create an Architecture Decision Record describing the result.



\---



\## Build 13 — XiYanSQL Experiment



Configure the `sql-reasoner` logical alias for SQL-specialized model testing.



Compare against the general reasoning model on difficult SQL evaluations.



\---



\## Build 14 — Grounding and Provenance



Strengthen claim-to-result provenance.



Track:



claim

→ result field

→ query

→ source



Reject unsupported numerical claims.



\---



\## Build 15 — Langfuse



Add AI observability through an internal interface.



\---



\## Build 16 — Authentication and Authorization



Add identity integration boundary and OPA.



Use safe development identities/policies first.



\---



\## Build 17 — OpenMetadata



Add metadata/catalog integration.



\---



\## Build 18 — SGLang



Move production model route toward local SGLang infrastructure.



Measure:



\* TTFT

\* throughput

\* token rate

\* prefix-cache hit rate

\* GPU utilization

\* concurrency



\---



\## Build 19 — Complete Development Compose Stack



Create a reproducible Docker Compose environment for components that have actually been integrated.



Pin versions.



Add health checks.



\---



\## Build 20 — Kubernetes Target



Create production-oriented Kubernetes/Helm deployment structure.



Do not pretend that a single database pod equals production HA.



\---



\# Immediate Implementation Goal



Start by building a WORKING vertical slice, not the entire infrastructure ecosystem.



Priority:



Foundation

→ LLMGateway

→ LiteLLM

→ LangGraph

→ synthetic PostgreSQL

→ DatabaseGateway

→ SQLGlot

→ first end-to-end analytical question



Only after that works should heavier enterprise services be introduced.



\---



\# Codex Working Rules



When working in this repository:



1\. Read this file completely.

2\. Respect the current implementation stage.

3\. Implement working software, not merely diagrams.

4\. Do not prematurely build future stages.

5\. Keep external integrations behind adapters.

6\. Use mature open-source infrastructure where it reduces custom code.

7\. Do not add a dependency without a concrete reason.

8\. Run tests after meaningful changes.

9\. Run Ruff and mypy where applicable.

10\. Report failed tests honestly.

11\. Never weaken security to make a test pass.

12\. Never commit secrets.

13\. Never use real confidential company data during development unless explicitly approved.

14\. Do not create large unused abstraction layers.

15\. Keep code understandable to new engineers.

16\. Document architecturally important decisions.

17\. Prefer a functioning vertical slice over many unfinished services.



\---



\# Immediate Task



This repository is currently new.



Begin with the foundation and continue toward the first working vertical slice.



The immediate development sequence is:



1\. project foundation

2\. configuration

3\. LLM abstraction

4\. LiteLLM integration

5\. LangGraph skeleton

6\. synthetic PostgreSQL

7\. DatabaseGateway

8\. SQLGlot

9\. first end-to-end database question

10\. automated tests



Do not introduce Wren, Cube, OpenMetadata, OPA, SGLang, or Kubernetes before the first vertical slice works unless a concrete dependency requires it.



