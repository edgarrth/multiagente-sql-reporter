from __future__ import annotations

from typing import Any

from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache
from axiz.pe.sql_agent.tools.example_selector import ExampleSelectorTool
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool
from axiz.pe.sql_agent.tools.semantic_context_projection import SemanticContextProjector


class SemanticExplorerAgent:
    """Catalog-driven semantic retrieval with bounded context projections and cache."""

    def __init__(
        self,
        catalog: SemanticCatalogTool,
        examples: ExampleSelectorTool,
        cache: AgentResponseCache | None = None,
        projector: SemanticContextProjector | None = None,
    ) -> None:
        self.catalog = catalog
        self.examples = examples
        self.cache = cache
        self.projector = projector or SemanticContextProjector()

    async def explore(
        self,
        question: str,
        domain: str,
        *,
        compact: bool = False,
        catalog_focus: list[str] | None = None,
        max_documents: int = 12,
        max_examples: int = 4,
    ) -> dict[str, Any]:
        cache_payload = {
            "contract_version": "semantic-retrieval-v4",
            "question": question,
            "domain": domain,
            "compact": compact,
            "catalog_focus": list(catalog_focus or []),
            "max_documents": max_documents,
            "max_examples": max_examples,
            "projector": self.projector.configuration(),
            "catalog_fingerprint": self.catalog.fingerprint(),
        }
        if self.cache is not None:
            lookup = await self.cache.get("semantic-context", cache_payload)
            if lookup.hit and lookup.value:
                return dict(lookup.value)

        hits = self.catalog.search(question, domain=domain, limit=max_documents)
        selected_examples = self.examples.select(
            question, domain=domain, limit=max_examples
        )
        full_context = {
            "domain_definition": self.catalog.get_domain(domain)["domain"],
            "catalog_hits": hits,
            "allowed_sources": self.catalog.allowed_sources(domain),
            "query_policy": self.catalog.policies(domain),
            "semantic_symbols": self.catalog.semantic_symbols(domain),
            "selected_examples": selected_examples,
        }
        result = (
            self.projector.project(
                question=question,
                full_context=full_context,
                catalog_focus=catalog_focus,
            )
            if compact
            else full_context
        )
        if self.cache is not None:
            await self.cache.set(
                "semantic-context",
                cache_payload,
                result,
                ttl_seconds=900,
            )
        return result
