from __future__ import annotations

from typing import Any

from axiz.pe.sql_agent.tools.example_selector import ExampleSelectorTool
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool


class SemanticExplorerAgent:
    """Tool-using specialist. Its behavior is entirely catalog-driven."""

    def __init__(
        self,
        catalog: SemanticCatalogTool,
        examples: ExampleSelectorTool,
    ) -> None:
        self.catalog = catalog
        self.examples = examples

    async def explore(self, question: str, domain: str) -> dict[str, Any]:
        hits = self.catalog.search(question, domain=domain, limit=12)
        selected_examples = self.examples.select(question, domain=domain, limit=4)
        return {
            "domain_definition": self.catalog.get_domain(domain)["domain"],
            "catalog_hits": hits,
            "allowed_sources": self.catalog.allowed_sources(domain),
            "query_policy": self.catalog.policies(domain),
            "semantic_symbols": self.catalog.semantic_symbols(domain),
            "selected_examples": selected_examples,
        }
