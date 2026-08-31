"""Datasource schema scanning and fingerprinting.

Scanning reads **metadata only**. Table names, column names, types, keys, and
comments describe the shape of a database; rows describe its contents. Only the
former is needed to propose what a table means, and only the former is sent to a
model, so onboarding a datasource never ships business data to a third party.

The fingerprint is the mechanism that makes approved knowledge durable across
schema change. It is computed from the structural facts a semantic mapping
depends on, so a mapping can be checked against it later: mappings whose
objects survive stay CONFIRMED, and only those whose objects disappeared or
changed shape become STALE. Nothing approved is ever silently deleted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.data.gateway import TableMetadata


@dataclass(frozen=True, slots=True)
class ScannedColumn:
    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool
    description: str = ""

    def fingerprint_payload(self) -> dict[str, Any]:
        # Description is deliberately excluded: an edited comment is not a
        # structural change and must not invalidate approved mappings.
        return {
            "name": self.name,
            "data_type": self.data_type.casefold(),
            "nullable": self.nullable,
            "primary_key": self.is_primary_key,
        }


@dataclass(frozen=True, slots=True)
class ScannedRelationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    #: Inferred only where it is safe to do so from constraints alone.
    cardinality: str | None = None

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "from": f"{self.from_table}.{self.from_column}",
            "to": f"{self.to_table}.{self.to_column}",
        }


@dataclass(frozen=True, slots=True)
class ScannedTable:
    schema_name: str
    table_name: str
    description: str
    columns: tuple[ScannedColumn, ...]
    primary_key: tuple[str, ...]
    object_type: str = "table"

    @property
    def identifier(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "object_type": self.object_type,
            "primary_key": sorted(self.primary_key),
            "columns": sorted(
                (column.fingerprint_payload() for column in self.columns),
                key=lambda payload: str(payload["name"]),
            ),
        }


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    tables: tuple[ScannedTable, ...]
    relationships: tuple[ScannedRelationship, ...]

    @property
    def fingerprint(self) -> str:
        payload = {
            "tables": sorted(
                (table.fingerprint_payload() for table in self.tables),
                key=lambda item: str(item["identifier"]),
            ),
            "relationships": sorted(
                (rel.fingerprint_payload() for rel in self.relationships),
                key=lambda item: (str(item["from"]), str(item["to"])),
            ),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def table(self, identifier: str) -> ScannedTable | None:
        return next(
            (table for table in self.tables if table.identifier == identifier), None
        )

    def column(self, identifier: str, column_name: str) -> ScannedColumn | None:
        table = self.table(identifier)
        if table is None:
            return None
        folded = column_name.casefold()
        return next(
            (column for column in table.columns if column.name.casefold() == folded),
            None,
        )

    def discovery_payload(self) -> dict[str, Any]:
        """Metadata handed to the model. Contains no row data by construction."""
        return {
            "tables": [
                {
                    "identifier": table.identifier,
                    "description": table.description,
                    "columns": [
                        {
                            "name": column.name,
                            "data_type": column.data_type,
                            "nullable": column.nullable,
                            "primary_key": column.is_primary_key,
                            "description": column.description,
                        }
                        for column in table.columns
                    ],
                }
                for table in self.tables
            ],
            "relationships": [
                {
                    "from": f"{rel.from_table}.{rel.from_column}",
                    "to": f"{rel.to_table}.{rel.to_column}",
                    "cardinality": rel.cardinality,
                }
                for rel in self.relationships
            ],
        }


class SchemaScanner:
    """Turns gateway table metadata into a fingerprinted snapshot.

    Reads from the same authorized metadata path the analytics flow uses, so a
    scan can never see more of a database than the caller is entitled to.
    """

    def scan(self, tables: list[TableMetadata]) -> SchemaSnapshot:
        scanned: list[ScannedTable] = []
        relationships: list[ScannedRelationship] = []

        for table in tables:
            primary_key = tuple(table.primary_key)
            columns = tuple(
                ScannedColumn(
                    name=column.name,
                    data_type=column.data_type,
                    nullable=column.nullable,
                    is_primary_key=column.primary_key or column.name in primary_key,
                    description=column.description,
                )
                for column in table.column_metadata
            )
            scanned.append(
                ScannedTable(
                    schema_name=table.schema_name,
                    table_name=table.table_name,
                    description=table.description,
                    columns=columns,
                    primary_key=primary_key,
                    object_type=table.object_type,
                )
            )
            relationships.extend(self._relationships_of(table, primary_key))

        return SchemaSnapshot(
            tables=tuple(scanned),
            relationships=tuple(relationships),
        )

    def _relationships_of(
        self, table: TableMetadata, primary_key: tuple[str, ...]
    ) -> list[ScannedRelationship]:
        found: list[ScannedRelationship] = []
        for foreign_key in table.foreign_keys:
            for column, referenced in zip(
                foreign_key.columns, foreign_key.referenced_columns, strict=False
            ):
                found.append(
                    ScannedRelationship(
                        from_table=table.identifier,
                        from_column=column,
                        to_table=foreign_key.referenced_table,
                        to_column=referenced,
                        cardinality=self._cardinality(column, primary_key),
                    )
                )
        return found

    @staticmethod
    def _cardinality(column: str, primary_key: tuple[str, ...]) -> str | None:
        """Infer cardinality only where constraints make it certain.

        A foreign key that is also the whole primary key is one-to-one; any
        other foreign key is many-to-one. Anything less certain is left None
        rather than guessed, because a wrong cardinality would produce a wrong
        join in every query built on it.
        """
        if primary_key == (column,):
            return "one_to_one"
        if column in primary_key:
            return None
        return "many_to_one"
