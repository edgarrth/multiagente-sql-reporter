from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

TOKEN_PATTERN = re.compile(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9_]+")


def tokenize(value: str) -> set[str]:
    return {token.lower() for token in TOKEN_PATTERN.findall(value)}


@dataclass(frozen=True)
class CatalogDocument:
    path: str
    kind: str
    domain: str
    content: dict[str, Any]
    search_text: str


class SemanticCatalogTool:
    """Loads domain definitions dynamically; adding YAML files requires no code changes."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._documents: list[CatalogDocument] = []
        self._domains: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        self._documents.clear()
        self._domains.clear()
        domains_root = self.root / "domains"
        if not domains_root.exists():
            raise FileNotFoundError(f"Semantic catalog not found: {domains_root}")

        for domain_dir in sorted(path for path in domains_root.iterdir() if path.is_dir()):
            domain_file = domain_dir / "domain.yaml"
            if not domain_file.exists():
                continue
            domain_data = self._read_yaml(domain_file)
            domain_name = str(domain_data["name"])
            self._domains[domain_name] = domain_data
            self._append_document(domain_file, "domain", domain_name, domain_data)

            for file in sorted(domain_dir.rglob("*.yaml")):
                if file == domain_file:
                    continue
                data = self._read_yaml(file)
                kind = file.parent.name.rstrip("s")
                self._append_document(file, kind, domain_name, data)

        global_root = self.root / "global"
        if global_root.exists():
            for file in sorted(global_root.rglob("*.yaml")):
                self._append_document(file, "global", "global", self._read_yaml(file))

    def list_domains(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": data.get("description", ""),
                "aliases": data.get("aliases", []),
            }
            for name, data in sorted(self._domains.items())
        ]

    def get_domain(self, domain: str) -> dict[str, Any]:
        if domain not in self._domains:
            raise KeyError(f"Unknown semantic domain: {domain}")
        documents = [
            {"path": doc.path, "kind": doc.kind, "content": doc.content}
            for doc in self._documents
            if doc.domain in {domain, "global"}
        ]
        return {"domain": self._domains[domain], "documents": documents}

    def search(self, query: str, domain: str, limit: int = 12) -> list[dict[str, Any]]:
        query_tokens = tokenize(query)
        scored: list[tuple[float, CatalogDocument]] = []
        for doc in self._documents:
            if doc.domain not in {domain, "global"}:
                continue
            document_tokens = tokenize(doc.search_text)
            overlap = len(query_tokens & document_tokens)
            exact_bonus = 3 if query.lower() in doc.search_text.lower() else 0
            domain_bonus = 1 if doc.domain == domain else 0
            score = overlap + exact_bonus + domain_bonus
            if score > 0:
                scored.append((float(score), doc))
        scored.sort(key=lambda item: (-item[0], item[1].path))
        return [
            {
                "score": score,
                "path": doc.path,
                "kind": doc.kind,
                "content": doc.content,
            }
            for score, doc in scored[:limit]
        ]

    def allowed_sources(self, domain: str) -> list[str]:
        data = self.get_domain(domain)["domain"]
        return [str(item) for item in data.get("allowed_sources", [])]

    def policies(self, domain: str) -> dict[str, Any]:
        return dict(self.get_domain(domain)["domain"].get("query_policy", {}))

    def semantic_symbols(self, domain: str) -> dict[str, list[dict[str, Any]]]:
        dimensions: list[dict[str, Any]] = []
        metrics: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        for document in self.get_domain(domain)["documents"]:
            content = document["content"]
            if document["kind"] == "entity":
                source = content.get("source")
                if source:
                    sources.append({"name": content.get("name"), "source": source})
                dimensions.extend(
                    item for item in content.get("dimensions", []) if isinstance(item, dict)
                )
                metrics.extend(
                    item for item in content.get("measures", []) if isinstance(item, dict)
                )
            if document["kind"] == "metric":
                metrics.extend(
                    item for item in content.get("metrics", []) if isinstance(item, dict)
                )
        return {
            "dimensions": self._deduplicate_symbols(dimensions),
            "metrics": self._deduplicate_symbols(metrics),
            "sources": self._deduplicate_symbols(sources),
        }

    @staticmethod
    def _deduplicate_symbols(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in items:
            key = (
                str(item.get("name") or ""),
                str(item.get("column") or ""),
                str(item.get("source") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(dict(item))
        return result

    def _append_document(
        self,
        file: Path,
        kind: str,
        domain: str,
        content: dict[str, Any],
    ) -> None:
        search_text = json.dumps(content, ensure_ascii=False, default=str)
        self._documents.append(
            CatalogDocument(
                path=str(file.relative_to(self.root)),
                kind=kind,
                domain=domain,
                content=content,
                search_text=search_text,
            )
        )

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Catalog document must be an object: {path}")
        return data
