from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from axiz.pe.sql_agent.models.contracts import CostValidation, QueryResult


class QueryEngineCapabilities(BaseModel):
    engine: str
    dialect: str
    supports_explain: bool = True
    supports_read_only_transactions: bool = True
    supports_relation_size: bool = False
    supports_statement_timeout: bool = True
    metadata: dict[str, str] = Field(default_factory=dict)


class QueryEngineHealth(BaseModel):
    healthy: bool
    engine: str
    dialect: str
    latency_ms: float | None = None
    message: str | None = None


class QueryEngine(ABC):
    """Provider-neutral contract consumed by the LangGraph workflow.

    Implementations own connection handling, cost estimation and read-only execution.
    The workflow never imports a vendor driver directly.
    """

    @property
    @abstractmethod
    def capabilities(self) -> QueryEngineCapabilities:
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> QueryEngineHealth:
        raise NotImplementedError

    async def ping(self) -> bool:
        return (await self.health()).healthy

    @abstractmethod
    async def estimate_cost(self, sql: str, tables: list[str]) -> CostValidation:
        raise NotImplementedError

    @abstractmethod
    async def execute(self, sql: str) -> QueryResult:
        raise NotImplementedError

    async def close(self) -> None:
        """Release provider-specific pools when an implementation owns them."""
