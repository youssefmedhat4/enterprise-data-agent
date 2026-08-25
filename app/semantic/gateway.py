from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.agent.context import AnalyticalContext
from app.data.gateway import TableMetadata


@dataclass(frozen=True)
class SemanticDefinition:
    identifier: str
    name: str
    description: str
    expression: str
    tables: tuple[str, ...]
    required_columns: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticMeasure:
    identifier: str
    name: str
    description: str
    expression: str
    tables: tuple[str, ...]
    required_columns: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    kind: Literal["measure", "calculated_field"] = "measure"


@dataclass(frozen=True)
class SemanticContext:
    tables: list[TableMetadata]
    definitions: list[SemanticDefinition] = field(default_factory=list)
    measures: list[SemanticMeasure] = field(default_factory=list)
    selection_reasons: dict[str, tuple[str, ...]] = field(default_factory=dict)
    provider: Literal["inmemory", "wren"] = "inmemory"
    retrieval_latency_ms: float = 0.0
    model_ids: list[str] = field(default_factory=list)
    relationship_ids: list[str] = field(default_factory=list)
    context_size_chars: int = 0

    @property
    def table_ids(self) -> list[str]:
        return [table.identifier for table in self.tables]

    @property
    def definition_ids(self) -> list[str]:
        return [definition.identifier for definition in self.definitions]

    @property
    def measure_ids(self) -> list[str]:
        return [measure.identifier for measure in self.measures]


class SemanticGatewayError(RuntimeError):
    """Base error for semantic-provider failures."""


class SemanticProviderUnavailableError(SemanticGatewayError):
    """Raised when the selected semantic provider cannot serve context."""


class SemanticGateway(Protocol):
    async def retrieve_context(
        self,
        *,
        question: str,
        available_tables: list[TableMetadata],
        prior_context: AnalyticalContext | None,
    ) -> SemanticContext:
        """Select relevant schema and governed business definitions."""
