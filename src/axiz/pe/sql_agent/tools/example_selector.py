from __future__ import annotations

from typing import Any

from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool, tokenize


class ExampleSelectorTool:
    def __init__(self, catalog: SemanticCatalogTool) -> None:
        self.catalog = catalog

    def select(self, question: str, domain: str, limit: int = 4) -> list[dict[str, Any]]:
        question_tokens = tokenize(question)
        examples: list[tuple[float, dict[str, Any]]] = []
        domain_context = self.catalog.get_domain(domain)
        for document in domain_context["documents"]:
            if document["kind"] != "example":
                continue
            content = document["content"]
            candidates = content.get("examples", [content])
            for candidate in candidates:
                candidate_text = " ".join(
                    str(candidate.get(field, ""))
                    for field in ("question", "intent", "sql", "notes")
                )
                overlap = len(question_tokens & tokenize(candidate_text))
                if overlap:
                    examples.append((float(overlap), candidate))
        examples.sort(key=lambda item: -item[0])
        return [example for _, example in examples[:limit]]
