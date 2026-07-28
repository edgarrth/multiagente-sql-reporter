from __future__ import annotations

import hashlib
import json
from typing import Any

from axiz.pe.sql_agent.tools.semantic_catalog import tokenize


class SemanticContextProjector:
    """Build a bounded, task-specific semantic context for LLM stages.

    The projector is domain-neutral: relevance is derived from token overlap, symbol metadata,
    document score and explicit task focus. It never changes permissions or the source allowlist.
    """

    def __init__(
        self,
        *,
        max_catalog_documents: int = 4,
        max_examples: int = 1,
        max_metrics: int = 10,
        max_dimensions: int = 12,
        max_document_items: int = 8,
        max_source_contracts: int = 3,
    ) -> None:
        self.max_catalog_documents = max(1, max_catalog_documents)
        self.max_examples = max(0, max_examples)
        self.max_metrics = max(1, max_metrics)
        self.max_dimensions = max(1, max_dimensions)
        self.max_document_items = max(1, max_document_items)
        self.max_source_contracts = max(1, max_source_contracts)

    def configuration(self) -> dict[str, int | str]:
        return {
            "contract_version": "semantic-context-v8",
            "max_catalog_documents": self.max_catalog_documents,
            "max_examples": self.max_examples,
            "max_metrics": self.max_metrics,
            "max_dimensions": self.max_dimensions,
            "max_document_items": self.max_document_items,
            "max_source_contracts": self.max_source_contracts,
        }

    @staticmethod
    def _document_kind_priority(hit: dict[str, Any]) -> float:
        kind = str(hit.get("kind") or "").lower()
        if kind.startswith("trusted_quer"):
            return 4.0
        if kind == "metric":
            return 3.5
        if kind.startswith("entit"):
            return 3.0
        if kind in {"join", "quality"}:
            return 2.0
        if kind == "domain":
            return 1.5
        if kind == "global":
            return 0.25
        if kind.startswith("example"):
            return 0.0
        return 1.0

    @staticmethod
    def _score(value: Any, query_tokens: set[str], base: float = 0.0) -> float:
        text = json.dumps(value, ensure_ascii=False, default=str)
        overlap = len(tokenize(text) & query_tokens)
        return base + float(overlap)

    @staticmethod
    def _compact_symbol(item: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "name",
            "column",
            "source",
            "type",
            "description",
            "expression",
            "sql",
            "certification",
            "format",
            "synonyms",
            "allowed_values",
        }
        return {key: value for key, value in item.items() if key in allowed}

    def _select_symbols(
        self,
        items: list[dict[str, Any]],
        query_tokens: set[str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        ranked = sorted(
            (
                (self._score(item, query_tokens), index, self._compact_symbol(item))
                for index, item in enumerate(items)
            ),
            key=lambda row: (-row[0], row[1]),
        )
        relevant = [item for score, _, item in ranked if score > 0]
        if not relevant:
            relevant = [item for _, _, item in ranked]
        return relevant[:limit]

    def _compact_document(self, hit: dict[str, Any]) -> dict[str, Any]:
        content = dict(hit.get("content") or {})
        compact: dict[str, Any] = {}
        for key in (
            "name",
            "description",
            "source",
            "grain",
            "primary_key",
            "timezone",
            "freshness",
            "certification_rules",
            "join_rules",
            "relationships",
            "quality_rules",
            "relative_periods",
            "rules",
            "trusted_queries",
            "metrics",
            "dimensions",
            "measures",
            "terms",
        ):
            if key not in content:
                continue
            value = content[key]
            if isinstance(value, list):
                value = value[: self.max_document_items]
            compact[key] = value
        return {
            "score": hit.get("score"),
            "path": hit.get("path"),
            "kind": hit.get("kind"),
            "content": compact,
        }


    def _select_source_contracts(
        self,
        contracts: dict[str, Any],
        query_tokens: set[str],
        *,
        selected_hits: list[dict[str, Any]],
        selected_examples: list[dict[str, Any]],
        required_sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return only the source contracts most relevant to the current task.

        `allowed_sources` remains complete and is still enforced by the deterministic security
        validator. The LLM receives a smaller candidate set so unrelated schemas do not consume
        tokens or encourage cross-view column mixing.
        """
        hit_text = json.dumps(selected_hits, ensure_ascii=False, default=str).lower()
        example_text = json.dumps(
            selected_examples, ensure_ascii=False, default=str
        ).lower()
        ranked: list[tuple[float, str, Any]] = []
        for source, contract in contracts.items():
            source_text = str(source).lower()
            name = str((contract or {}).get("name") or "").lower()
            evidence_bonus = 0.0
            if source_text in example_text:
                evidence_bonus += 16.0
            if source_text in hit_text:
                evidence_bonus += 8.0
            if name and name in example_text:
                evidence_bonus += 8.0
            if name and name in hit_text:
                evidence_bonus += 4.0
            score = self._score(contract, query_tokens, evidence_bonus)
            ranked.append((score, str(source), contract))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        required_lookup = {str(source).lower() for source in required_sources or []}
        selected: list[tuple[float, str, Any]] = [
            row for row in ranked if row[1].lower() in required_lookup
        ]
        target_size = max(self.max_source_contracts, len(selected))
        for row in ranked:
            if any(existing[1] == row[1] for existing in selected):
                continue
            if len(selected) >= target_size:
                break
            selected.append(row)
        return {source: contract for _, source, contract in selected}

    @staticmethod
    def _compact_example(example: dict[str, Any]) -> dict[str, Any]:
        return {
            key: example[key]
            for key in ("id", "question", "intent", "sql", "notes")
            if key in example
        }

    def project(
        self,
        *,
        question: str,
        full_context: dict[str, Any],
        catalog_focus: list[str] | None = None,
        required_sources: list[str] | None = None,
    ) -> dict[str, Any]:
        focus = [str(item) for item in catalog_focus or []]
        query_tokens = tokenize(" ".join([question, *focus]))
        symbols = dict(full_context.get("semantic_symbols") or {})

        hits = sorted(
            list(full_context.get("catalog_hits") or []),
            key=lambda item: (
                -(
                    self._score(
                        item.get("content") or {},
                        query_tokens,
                        float(item.get("score") or 0),
                    )
                    + self._document_kind_priority(item)
                ),
                str(item.get("path") or ""),
            ),
        )[: self.max_catalog_documents]
        examples = sorted(
            list(full_context.get("selected_examples") or []),
            key=lambda item: -self._score(item, query_tokens),
        )[: self.max_examples]

        domain_definition = dict(full_context.get("domain_definition") or {})
        domain_definition = {
            key: domain_definition[key]
            for key in (
                "name",
                "version",
                "description",
                "aliases",
                "owner",
                "certification_rules",
                "data_classification",
            )
            if key in domain_definition
        }
        compact_hits = [self._compact_document(item) for item in hits]
        compact_examples = [self._compact_example(item) for item in examples]
        selected_contracts = self._select_source_contracts(
            dict(full_context.get("source_contracts") or {}),
            query_tokens,
            selected_hits=compact_hits,
            selected_examples=compact_examples,
            required_sources=required_sources,
        )
        selected_source_names = set(selected_contracts)
        source_symbols = [
            item
            for item in list(symbols.get("sources") or [])
            if str(item.get("source") or "") in selected_source_names
        ]

        projected = {
            "domain_definition": domain_definition,
            "allowed_sources": list(full_context.get("allowed_sources") or []),
            "query_policy": dict(full_context.get("query_policy") or {}),
            "source_contracts": selected_contracts,
            "calendar_context": dict(full_context.get("calendar_context") or {}),
            "semantic_symbols": {
                "metrics": self._select_symbols(
                    list(symbols.get("metrics") or []),
                    query_tokens,
                    limit=self.max_metrics,
                ),
                "dimensions": self._select_symbols(
                    list(symbols.get("dimensions") or []),
                    query_tokens,
                    limit=self.max_dimensions,
                ),
                "sources": self._select_symbols(
                    source_symbols or list(symbols.get("sources") or []),
                    query_tokens,
                    limit=self.max_source_contracts,
                ),
            },
            "catalog_hits": compact_hits,
            "selected_examples": compact_examples,
        }
        serialized = json.dumps(projected, ensure_ascii=False, sort_keys=True, default=str)
        projected["projection_metadata"] = {
            "contract_version": "semantic-context-v8",
            "source_catalog_documents": len(full_context.get("catalog_hits") or []),
            "projected_catalog_documents": len(projected["catalog_hits"]),
            "source_examples": len(full_context.get("selected_examples") or []),
            "projected_examples": len(projected["selected_examples"]),
            "approx_characters": len(serialized),
            "fingerprint": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        }
        return projected

    @staticmethod
    def for_review(context: dict[str, Any]) -> dict[str, Any]:
        """Further reduce a SQL-generation context for proposal review."""
        return {
            "domain_definition": context.get("domain_definition") or {},
            "allowed_sources": context.get("allowed_sources") or [],
            "query_policy": context.get("query_policy") or {},
            "source_contracts": context.get("source_contracts") or {},
            "calendar_context": context.get("calendar_context") or {},
            "semantic_symbols": context.get("semantic_symbols") or {},
            "projection_metadata": context.get("projection_metadata") or {},
        }
