"""Strict contracts for the datasource-scoped knowledge registry."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ApprovalStatus(StrEnum):
    """Lifecycle of a piece of proposed knowledge.

    AI proposals are never truth on arrival. Only CONFIRMED knowledge may be
    used by governed runtime; STALE marks knowledge invalidated by a schema
    change, which is preserved for review rather than deleted.
    """

    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    STALE = "STALE"


class DataSourceStatus(StrEnum):
    REGISTERED = "REGISTERED"
    SCANNING = "SCANNING"
    READY = "READY"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


type Cardinality = Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]


class DataSource(StrictContract):
    """A registered analytics database.

    `connection_ref` is a reference to a secret (an environment variable name),
    never a DSN or password. Nothing in this contract is secret, so it is safe
    to serialize to the admin UI.
    """

    id: UUID
    name: str = Field(min_length=1, max_length=200)
    database_type: str = Field(min_length=1, max_length=50)
    connection_ref: str = Field(min_length=1, max_length=200)
    status: DataSourceStatus = DataSourceStatus.REGISTERED
    #: Schemas this database exposes. Scoped per datasource rather than per
    #: process, so one database's configuration cannot govern another's.
    allowed_schemas: tuple[str, ...] = ("analytics",)
    schema_fingerprint: str | None = None
    is_default: bool = False
    created_at: datetime
    updated_at: datetime
    last_scanned_at: datetime | None = None

    @field_validator("connection_ref")
    @classmethod
    def _reject_connection_strings(cls, value: str) -> str:
        """Refuse anything that looks like a DSN or an inline credential.

        Mirrors the database CHECK constraint so a bad value is rejected before
        it reaches the driver, and so the error names the field rather than
        surfacing a raw constraint violation.
        """
        lowered = value.lower()
        if "://" in lowered or "password" in lowered:
            raise ValueError(
                "connection_ref must be a secret reference such as an "
                "environment variable name, not a connection string."
            )
        return value


class SemanticEntity(StrictContract):
    """A table interpreted as a business concept, e.g. `staff` -> Employee."""

    id: UUID
    data_source_id: UUID
    source_schema: str = Field(min_length=1)
    source_table: str = Field(min_length=1)
    entity_name: str = Field(min_length=1)
    description: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason_code: str | None = None
    status: ApprovalStatus = ApprovalStatus.PROPOSED
    schema_fingerprint: str | None = None


class SemanticAttribute(StrictContract):
    """A column interpreted as a business concept."""

    id: UUID
    data_source_id: UUID
    entity_id: UUID
    source_column: str = Field(min_length=1)
    concept_name: str = Field(min_length=1)
    description: str | None = None
    data_type: str | None = None
    is_identifier: bool = False
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: ApprovalStatus = ApprovalStatus.PROPOSED


class SemanticRelationship(StrictContract):
    """A join between two entities, interpreted as a business relationship."""

    id: UUID
    data_source_id: UUID
    from_entity_id: UUID
    to_entity_id: UUID
    from_column: str = Field(min_length=1)
    to_column: str = Field(min_length=1)
    relationship_name: str = Field(min_length=1)
    cardinality: Cardinality | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: ApprovalStatus = ApprovalStatus.PROPOSED
