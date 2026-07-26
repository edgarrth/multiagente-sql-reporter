from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SqlNormalizationResult:
    sql: str
    changed: bool
    transformations: tuple[str, ...] = ()


class SqlDialectNormalizer:
    """Canonicalize equivalent SQL syntax before AST parsing.

    The normalizer is intentionally conservative: it only rewrites expressions with an
    equivalent meaning in the configured engine dialect. It never removes clauses, comments,
    predicates, statements, or identifiers, so SQLGlot remains the authority for security.
    """

    _POSTGRES_INTERVAL_STRING = re.compile(
        r"(?i)\bINTERVAL\s+'\s*([+-]?\d+(?:\.\d+)?)\s+"
        r"(YEARS?|MONTHS?|WEEKS?|DAYS?|HOURS?|MINUTES?|SECONDS?)\s*'"
    )
    _POSTGRES_INTERVAL_SEPARATE = re.compile(
        r"(?i)\bINTERVAL\s+'?\s*([+-]?\d+(?:\.\d+)?)\s*'?\s+"
        r"(YEARS?|MONTHS?|WEEKS?|DAYS?|HOURS?|MINUTES?|SECONDS?)\b"
    )
    _POSTGRES_CURRENT_TS_TZ = re.compile(
        r"(?i)\bCURRENT_TIMESTAMP(?:\s*\(\s*\))?\s+AT\s+TIME\s+ZONE\s+"
        r"('(?:[^']|'')*')"
    )

    def __init__(self, dialect: str) -> None:
        self.dialect = dialect.lower().strip()

    def normalize(self, sql: str) -> SqlNormalizationResult:
        candidate = sql.strip()
        transformations: list[str] = []

        if candidate.startswith("```") and candidate.endswith("```"):
            lines = candidate.splitlines()
            if lines and lines[0].strip().lower() in {"```", "```sql", "```postgresql"}:
                candidate = "\n".join(lines[1:-1]).strip()
                transformations.append("removed_markdown_fence")

        candidate = candidate.rstrip().rstrip(";").strip()

        if self.dialect in {"postgres", "postgresql"}:
            candidate, changed = self._POSTGRES_CURRENT_TS_TZ.subn(
                lambda match: f"TIMEZONE({match.group(1)}, CURRENT_TIMESTAMP)",
                candidate,
            )
            if changed:
                transformations.append("canonicalized_current_timestamp_timezone")

            candidate, changed = self._POSTGRES_INTERVAL_STRING.subn(
                lambda match: (
                    f"INTERVAL '{match.group(1)}' {self._singular_unit(match.group(2))}"
                ),
                candidate,
            )
            if changed:
                transformations.append("canonicalized_interval_literal")

            # Handles model outputs such as INTERVAL 2 MONTHS or INTERVAL '2' MONTHS.
            candidate, changed = self._POSTGRES_INTERVAL_SEPARATE.subn(
                lambda match: (
                    f"INTERVAL '{match.group(1)}' {self._singular_unit(match.group(2))}"
                ),
                candidate,
            )
            if changed:
                transformations.append("canonicalized_interval_unit")

        return SqlNormalizationResult(
            sql=candidate,
            changed=bool(transformations) or candidate != sql.strip(),
            transformations=tuple(dict.fromkeys(transformations)),
        )

    @staticmethod
    def _singular_unit(unit: str) -> str:
        normalized = unit.upper()
        return normalized[:-1] if normalized.endswith("S") else normalized
