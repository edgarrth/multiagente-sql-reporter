from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FilterBooleanOperator(StrEnum):
    AND = "and"
    OR = "or"
    NOT = "not"


class SemanticMeasure(BaseModel):
    member: str = Field(min_length=1, max_length=300)
    alias: str | None = Field(default=None, max_length=300)
    aggregation: str | None = Field(default=None, max_length=80)


class SemanticDimension(BaseModel):
    member: str = Field(min_length=1, max_length=300)
    alias: str | None = Field(default=None, max_length=300)


class SemanticPredicate(BaseModel):
    member: str = Field(min_length=1, max_length=300)
    operator: str = Field(min_length=1, max_length=80)
    values: list[Any] = Field(default_factory=list, max_length=100)
    source: str = Field(default="user", max_length=80)


class SemanticFilterGroup(BaseModel):
    operator: FilterBooleanOperator = FilterBooleanOperator.AND
    expressions: list["SemanticPredicate | SemanticFilterGroup"] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_not_arity(self) -> "SemanticFilterGroup":
        if self.operator == FilterBooleanOperator.NOT and len(self.expressions) != 1:
            raise ValueError("A NOT filter group must contain exactly one expression")
        return self


class SemanticTimeRange(BaseModel):
    type: str = Field(default="relative", max_length=80)
    unit: str | None = Field(default=None, max_length=40)
    value: int | None = Field(default=None, ge=1, le=36500)
    periods: int | None = Field(default=None, ge=1, le=1200)
    start: str | None = Field(default=None, max_length=500)
    end: str | None = Field(default=None, max_length=500)
    exclude_current_period: bool | None = None
    include_current_partial_period: bool | None = None
    raw_expression: str | None = Field(default=None, max_length=4000)


class SemanticTimeFilter(BaseModel):
    member: str = Field(min_length=1, max_length=300)
    range: SemanticTimeRange
    timezone: str | None = Field(default=None, max_length=100)


class SemanticOrder(BaseModel):
    member: str = Field(min_length=1, max_length=300)
    direction: Literal["asc", "desc"] = "asc"


class QuerySpecReference(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)


class SemanticQuerySpec(BaseModel):
    schema_version: str = "1.1"
    spec_id: str = Field(min_length=1, max_length=120)
    version: int = Field(default=1, ge=1)
    semantic_model: str | None = Field(default=None, max_length=300)
    original_question: str = Field(default="", max_length=8000)
    raw_user_message: str = Field(default="", max_length=8000)
    interpretation: str = Field(default="", max_length=8000)
    measures: list[SemanticMeasure] = Field(default_factory=list, max_length=100)
    dimensions: list[SemanticDimension] = Field(default_factory=list, max_length=100)
    filters: SemanticFilterGroup | None = None
    time_filters: list[SemanticTimeFilter] = Field(default_factory=list, max_length=50)
    order_by: list[SemanticOrder] = Field(default_factory=list, max_length=100)
    limit: int | None = Field(default=None, ge=1, le=1_000_000)
    source_objects: list[str] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=100)

    @property
    def reference(self) -> QuerySpecReference:
        return QuerySpecReference(id=self.spec_id, version=self.version)


class QuerySpecPatchOperation(BaseModel):
    change_id: str = Field(min_length=1, max_length=80)
    operation: Literal[
        "set",
        "increase",
        "decrease",
        "add",
        "remove",
        "replace",
        "reorder",
        "regenerate",
    ]
    target: Literal[
        "limit",
        "time_window",
        "filter",
        "order",
        "grouping",
        "dimension",
        "metric",
        "source",
        "projection",
        "comparison",
        "other",
    ]
    member: str | None = Field(default=None, max_length=300)
    from_member: str | None = Field(default=None, max_length=300)
    to_member: str | None = Field(default=None, max_length=300)
    value: Any = None
    values: list[Any] = Field(default_factory=list, max_length=100)
    unit: str | None = Field(default=None, max_length=40)
    scope: str = Field(default="overall", max_length=80)
    direction: Literal["asc", "desc"] | None = None
    predicate_operator: str | None = Field(default=None, max_length=80)
    reason: str = Field(default="", max_length=800)
    derived: bool = False


class QuerySpecPatch(BaseModel):
    schema_version: str = "1.0"
    base: QuerySpecReference
    raw_user_message: str = Field(default="", max_length=8000)
    operations: list[QuerySpecPatchOperation] = Field(default_factory=list, max_length=50)
    preserve: list[str] = Field(default_factory=list, max_length=100)


class QuerySpecResolution(BaseModel):
    base: QuerySpecReference
    resolved: SemanticQuerySpec
    requested_patch: QuerySpecPatch
    derived_changes: list[QuerySpecPatchOperation] = Field(default_factory=list)


class CompiledSqlValidation(BaseModel):
    """Deterministic validation metadata attached after SQL generation.

    This contract is deliberately closed: it is never generated by the LLM and
    it must remain compatible with strict JSON Schema consumers.
    """

    model_config = ConfigDict(extra="forbid")

    parse_valid: bool = False
    order_dependencies_valid: bool = False
    query_spec_alignment_valid: bool = False
    projection_aliases: list[str] = Field(default_factory=list)
    invalid_order_references: list[str] = Field(default_factory=list)
    query_spec_violations: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)


class CompiledSqlArtifact(BaseModel):
    schema_version: str = "1.0"
    query_spec_ref: QuerySpecReference
    dialect: str
    sql: str
    sql_hash: str
    validation: CompiledSqlValidation = Field(default_factory=CompiledSqlValidation)
    execution_state: Literal[
        "candidate",
        "validated",
        "awaiting_approval",
        "executed",
        "rejected",
        "failed",
    ] = "candidate"


SemanticFilterGroup.model_rebuild()


def query_spec_contracts() -> dict[str, dict[str, Any]]:
    """Return JSON Schema for the canonical semantic query lifecycle."""
    return {
        "semantic_query_spec": SemanticQuerySpec.model_json_schema(),
        "query_spec_patch": QuerySpecPatch.model_json_schema(),
        "query_spec_resolution": QuerySpecResolution.model_json_schema(),
        "compiled_sql_artifact": CompiledSqlArtifact.model_json_schema(),
    }
