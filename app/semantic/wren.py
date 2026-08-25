import asyncio
import json
import re
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol, cast

from app.agent.context import AnalyticalContext
from app.data.gateway import TableMetadata
from app.semantic.gateway import (
    SemanticContext,
    SemanticDefinition,
    SemanticGateway,
    SemanticMeasure,
    SemanticProviderUnavailableError,
)

GENERIC_RETRIEVAL_TERMS = {
    "amount",
    "code",
    "date",
    "description",
    "end",
    "identifier",
    "name",
    "number",
    "start",
    "status",
}


@dataclass(frozen=True)
class WrenSnapshot:
    mdl: dict[str, Any]
    retrieval: dict[str, Any]
    instructions: str


class WrenContextClient(Protocol):
    async def retrieve(self, question: str) -> WrenSnapshot:
        """Retrieve the compiled MDL and question-specific Wren context."""


class MCPWrenContextClient:
    """Official MCP-client transport for Wren's local read-only context service."""

    def __init__(self, url: str, *, timeout_seconds: float) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds

    async def retrieve(self, question: str) -> WrenSnapshot:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise SemanticProviderUnavailableError(
                "The Wren MCP client dependency is not installed."
            ) from exc

        try:
            async with (
                asyncio.timeout(self._timeout_seconds),
                streamable_http_client(self._url) as (read_stream, write_stream, _),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                mdl = _tool_payload(await session.call_tool("get_mdl"))
                retrieval = _tool_payload(
                    await session.call_tool("get_context", {"question": question, "limit": 8})
                )
                instructions_payload = _tool_payload(await session.call_tool("get_instructions"))
        except SemanticProviderUnavailableError:
            raise
        except (OSError, TimeoutError, ConnectionError) as exc:
            raise SemanticProviderUnavailableError(
                "The configured Wren semantic service is unavailable."
            ) from exc
        except Exception as exc:
            raise SemanticProviderUnavailableError(
                "The configured Wren semantic service returned an invalid response."
            ) from exc

        instructions = instructions_payload.get("instructions", "")
        if not isinstance(instructions, str):
            instructions = ""
        return WrenSnapshot(mdl=mdl, retrieval=retrieval, instructions=instructions)


class WrenSemanticGateway(SemanticGateway):
    """Maps bounded Wren semantic context onto live physical database metadata."""

    def __init__(
        self,
        client: WrenContextClient,
        *,
        max_models: int = 6,
        project_id: str = "enterprise_analytics",
    ) -> None:
        self._client = client
        self._max_models = max_models
        self._project_id = project_id

    async def retrieve_context(
        self,
        *,
        question: str,
        available_tables: list[TableMetadata],
        prior_context: AnalyticalContext | None,
    ) -> SemanticContext:
        started_at = perf_counter()
        snapshot = await self._client.retrieve(question)
        models = _model_records(snapshot.mdl)
        relationships = _relationship_records(snapshot.mdl)
        available_by_id = {table.identifier: table for table in available_tables}
        model_to_table = {
            model["name"]: physical_id
            for model in models
            if (physical_id := _physical_identifier(model)) in available_by_id
        }
        context_text = _context_text(question, prior_context)
        retrieved_names = _retrieved_model_names(snapshot.retrieval)
        reasons: dict[str, set[str]] = defaultdict(set)
        scores: dict[str, int] = {}
        for model in models:
            name = _string(model.get("name"))
            physical_table_id = model_to_table.get(name)
            if physical_table_id is None:
                continue
            score, model_reasons = _model_score(model, context_text)
            if name in retrieved_names:
                score += 20
                model_reasons.add("wren_retrieval")
            if score > 0:
                scores[name] = score
                reasons[physical_table_id].update(model_reasons)

        selected_models = {
            name
            for name, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[
                : self._max_models
            ]
        }
        selected_models = _expand_relationship_paths(
            selected_models,
            relationships,
            model_to_table,
            reasons,
            max_models=self._max_models,
        )
        definitions = _selected_definitions(models, selected_models, context_text)
        measures = _selected_measures(models, selected_models, context_text, model_to_table)
        selected_models = _expand_semantic_dependencies(
            selected_models,
            [*definitions, *measures],
            model_to_table,
            reasons,
            max_models=self._max_models,
        )
        selected_models = _expand_relationship_paths(
            selected_models,
            relationships,
            model_to_table,
            reasons,
            max_models=self._max_models,
        )
        selected_table_ids = {
            model_to_table[name] for name in selected_models if name in model_to_table
        }
        selected_tables = [
            table for table in available_tables if table.identifier in selected_table_ids
        ]
        selected_relationships = [
            relationship
            for relationship in relationships
            if set(_string_list(relationship.get("models"))).issubset(selected_models)
        ]
        definitions = _selected_definitions(models, selected_models, context_text)
        measures = _selected_measures(models, selected_models, context_text, model_to_table)
        context_size = _context_size(selected_models, selected_relationships, definitions, measures)
        return SemanticContext(
            tables=selected_tables,
            definitions=definitions,
            measures=measures,
            selection_reasons={key: tuple(sorted(value)) for key, value in reasons.items()},
            provider="wren",
            retrieval_latency_ms=round((perf_counter() - started_at) * 1000, 3),
            model_ids=[f"wren:{self._project_id}:{name}" for name in sorted(selected_models)],
            relationship_ids=[
                f"wren:{self._project_id}:{_string(relationship.get('name'))}"
                for relationship in selected_relationships
            ],
            context_size_chars=context_size,
        )


def _tool_payload(result: Any) -> dict[str, Any]:
    if bool(getattr(result, "isError", False) or getattr(result, "is_error", False)):
        raise SemanticProviderUnavailableError("A Wren context tool reported an error.")
    structured = getattr(result, "structuredContent", None) or getattr(
        result, "structured_content", None
    )
    if isinstance(structured, Mapping):
        return dict(structured)
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return cast(dict[str, Any], payload)
    raise SemanticProviderUnavailableError("A Wren context tool returned no structured data.")


def _model_records(mdl: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _mapping_list(mdl.get("models"))


def _relationship_records(mdl: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _mapping_list(mdl.get("relationships"))


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _physical_identifier(model: Mapping[str, Any]) -> str:
    reference = model.get("tableReference") or model.get("table_reference")
    if not isinstance(reference, Mapping):
        return ""
    schema = _string(reference.get("schema"))
    table = _string(reference.get("table"))
    return f"{schema}.{table}" if schema and table else ""


def _context_text(question: str, prior_context: AnalyticalContext | None) -> str:
    values = [question]
    if prior_context is not None:
        values.extend(
            value
            for value in (
                prior_context.metric,
                *prior_context.dimensions,
                *prior_context.entities,
                prior_context.previous_question,
            )
            if value
        )
    return " ".join(values).casefold()


def _retrieved_model_names(retrieval: Mapping[str, Any]) -> set[str]:
    if _string(retrieval.get("strategy")).casefold() == "full":
        return set()
    results = retrieval.get("results")
    if not isinstance(results, list):
        return set()
    return {
        name
        for item in results
        if isinstance(item, Mapping)
        and (name := _string(item.get("model_name") or item.get("modelName")))
    }


def _model_score(model: Mapping[str, Any], context_text: str) -> tuple[int, set[str]]:
    score = 0
    reasons: set[str] = set()
    model_terms = _terms(model)
    if any(_contains(context_text, term) for term in model_terms):
        score += 10
        reasons.add("wren_model_match")
    for column in _mapping_list(model.get("columns")):
        if any(_contains(context_text, term) for term in _terms(column)):
            score += 3
            reasons.add("wren_column_match")
    return score, reasons


def _terms(item: Mapping[str, Any]) -> set[str]:
    properties = item.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    values = {
        _string(item.get("name")),
        _string(properties.get("description")),
    }
    values.update(_string_list(properties.get("aliases")))
    terms: set[str] = set()
    for value in values:
        normalized = value.casefold().replace("_", " ").strip()
        if not normalized:
            continue
        if normalized not in GENERIC_RETRIEVAL_TERMS:
            terms.add(normalized)
    return terms


def _contains(text: str, term: str) -> bool:
    if re.fullmatch(r"[a-z0-9 _-]+", term):
        return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text))
    return term in text


def _expand_relationship_paths(
    selected: set[str],
    relationships: list[dict[str, Any]],
    model_to_table: Mapping[str, str],
    reasons: dict[str, set[str]],
    *,
    max_models: int,
) -> set[str]:
    graph: dict[str, set[str]] = defaultdict(set)
    for relationship in relationships:
        models = _string_list(relationship.get("models"))
        if len(models) != 2 or any(model not in model_to_table for model in models):
            continue
        graph[models[0]].add(models[1])
        graph[models[1]].add(models[0])
    targets = sorted(selected)
    for index, start in enumerate(targets):
        for target in targets[index + 1 :]:
            for model in _shortest_path(graph, start, target)[1:-1]:
                if len(selected) >= max_models:
                    return selected
                selected.add(model)
                reasons[model_to_table[model]].add("wren_relationship_path")
    return selected


def _expand_semantic_dependencies(
    selected: set[str],
    semantic_items: Sequence[SemanticDefinition | SemanticMeasure],
    model_to_table: Mapping[str, str],
    reasons: dict[str, set[str]],
    *,
    max_models: int,
) -> set[str]:
    table_to_model = {table: model for model, table in model_to_table.items()}
    for table_id in sorted({table for item in semantic_items for table in item.tables}):
        model = table_to_model.get(table_id)
        if model is None or model in selected:
            continue
        if len(selected) >= max_models:
            break
        selected.add(model)
        reasons[table_id].add("wren_semantic_dependency")
    return selected


def _shortest_path(graph: Mapping[str, set[str]], start: str, target: str) -> list[str]:
    queue: deque[list[str]] = deque([[start]])
    visited = {start}
    while queue:
        path = queue.popleft()
        for neighbor in sorted(graph.get(path[-1], set())):
            if neighbor in visited:
                continue
            next_path = [*path, neighbor]
            if neighbor == target:
                return next_path
            visited.add(neighbor)
            queue.append(next_path)
    return []


def _selected_definitions(
    models: list[dict[str, Any]], selected_models: set[str], context_text: str
) -> list[SemanticDefinition]:
    definitions: list[SemanticDefinition] = []
    for model in models:
        if _string(model.get("name")) not in selected_models:
            continue
        properties = model.get("properties")
        if not isinstance(properties, Mapping):
            continue
        for definition in _mapping_list(properties.get("definitions")):
            aliases = tuple(_string_list(definition.get("aliases")))
            terms = (_string(definition.get("name")), *aliases)
            if not any(term and _contains(context_text, term.casefold()) for term in terms):
                continue
            identifier = _string(definition.get("identifier"))
            if not identifier:
                continue
            definitions.append(
                SemanticDefinition(
                    identifier=f"wren:{identifier}",
                    name=_string(definition.get("name")) or identifier,
                    description=_string(definition.get("description")),
                    expression=_string(definition.get("expression")),
                    tables=tuple(_string_list(definition.get("tables"))),
                    aliases=aliases,
                )
            )
    return definitions


def _selected_measures(
    models: list[dict[str, Any]],
    selected_models: set[str],
    context_text: str,
    model_to_table: Mapping[str, str],
) -> list[SemanticMeasure]:
    measures: list[SemanticMeasure] = []
    for model in models:
        model_name = _string(model.get("name"))
        if model_name not in selected_models:
            continue
        for column in _mapping_list(model.get("columns")):
            if not bool(column.get("isCalculated") or column.get("is_calculated")):
                continue
            properties = column.get("properties")
            properties = properties if isinstance(properties, Mapping) else {}
            aliases = tuple(_string_list(properties.get("aliases")))
            terms = (_string(column.get("name")).replace("_", " "), *aliases)
            if not any(term and _contains(context_text, term.casefold()) for term in terms):
                continue
            name = _string(column.get("name"))
            measures.append(
                SemanticMeasure(
                    identifier=f"wren:{model_name}.{name}",
                    name=name.replace("_", " ").title(),
                    description=_string(properties.get("description")),
                    expression=_string(column.get("expression")),
                    tables=(model_to_table[model_name],),
                    aliases=aliases,
                    kind="calculated_field",
                )
            )
    return measures


def _context_size(
    model_ids: set[str],
    relationships: Sequence[Mapping[str, Any]],
    definitions: Sequence[SemanticDefinition],
    measures: Sequence[SemanticMeasure],
) -> int:
    return (
        sum(len(value) for value in model_ids)
        + sum(len(json.dumps(item, default=str)) for item in relationships)
        + sum(len(item.description) + len(item.expression) for item in definitions)
        + sum(len(item.description) + len(item.expression) for item in measures)
    )


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
