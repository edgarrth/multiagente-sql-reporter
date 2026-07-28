from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SqlSnapshot(BaseModel):
    """Generic, AST-derived description of one SQL statement.

    The snapshot is an audit artifact, not an analytical contract and not an LLM input schema.
    It mirrors SQL structure without imposing a closed vocabulary of business filters, metrics,
    periods, or feedback targets.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "2.0"
    dialect: str
    statement_type: str = "SELECT"
    sources: list[str] = Field(default_factory=list)
    projections: list[str] = Field(default_factory=list)
    predicates: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    having: str | None = None
    order_by: list[str] = Field(default_factory=list)
    limit: int | None = None
    distinct: bool = False
    ctes: list[str] = Field(default_factory=list)


class CompiledSqlValidation(BaseModel):
    """Deterministic structural validation attached after SQL generation."""

    model_config = ConfigDict(extra="forbid")

    parse_valid: bool = False
    references_valid: bool = False
    projection_aliases: list[str] = Field(default_factory=list)
    invalid_order_references: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)


class CompiledSqlArtifact(BaseModel):
    """Executable SQL plus generic structural metadata and lifecycle state."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "2.0"
    dialect: str
    sql: str
    sql_hash: str
    snapshot: SqlSnapshot
    validation: CompiledSqlValidation = Field(default_factory=CompiledSqlValidation)
    execution_state: Literal[
        "candidate",
        "validated",
        "awaiting_approval",
        "executed",
        "rejected",
        "failed",
    ] = "candidate"


def sql_artifact_contracts() -> dict[str, dict[str, Any]]:
    return {
        "sql_snapshot": SqlSnapshot.model_json_schema(),
        "compiled_sql_artifact": CompiledSqlArtifact.model_json_schema(),
    }
