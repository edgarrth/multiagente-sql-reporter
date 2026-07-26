from __future__ import annotations

import re

from typing import Any

from axiz.pe.sql_agent.models.contracts import SqlFeedbackApplication


class SqlFeedbackApplier:
    """Apply deterministic, policy-bounded SQL changes requested during HITL.

    The LLM still regenerates the query for semantic changes, but structural changes
    such as LIMIT are enforced against the regenerated SQL so user feedback cannot be
    silently ignored.
    """

    _LIMIT_PATTERNS = (
        re.compile(
            r"(?ix)\b(?:sube|aumenta|incrementa|cambia|ajusta|pon|establece|modifica|reduce|baja)"
            r"[^\n]{0,45}?\b(?:el\s+)?l[ií]mite\b[^\d]{0,15}(\d[\d._,]*)"
        ),
        re.compile(
            r"(?ix)\b(?:l[ií]mite|limit|max(?:imo)?|m[aá]ximo|top)\b"
            r"\s*(?:de|a|en|=|:)?\s*(\d[\d._,]*)\b"
        ),
        re.compile(
            r"(?ix)\b(?:muestra|devuelve|retorna|trae|obt[eé]n)\b[^\n]{0,30}?"
            r"(\d[\d._,]*)\s+(?:filas|registros|resultados)\b"
        ),
        re.compile(
            r"(?ix)\b(\d[\d._,]*)\s+(?:filas|registros|resultados)\s+como\s+m[aá]ximo\b"
        ),
    )

    _INTERPRETATION_LIMIT_PATTERNS = (
        re.compile(r"(?i)\blimitad[oa]s?\s+a\s+\d[\d._,]*\s+(?:resultados|filas|registros)\b"),
        re.compile(r"(?i)\b(?:hasta|m[aá]ximo\s+de)\s+\d[\d._,]*\s+(?:resultados|filas|registros)\b"),
        re.compile(r"(?i)\btop\s+\d[\d._,]*\b"),
    )

    def __init__(self, dialect: str, max_rows: int) -> None:
        self.dialect = dialect
        self.max_rows = max_rows

    def apply(self, sql: str, feedback: str | None) -> SqlFeedbackApplication:
        requested_limit = self.extract_requested_limit(feedback)
        if requested_limit is None:
            return SqlFeedbackApplication(sql=sql.strip().rstrip(";"))

        applied_limit = min(requested_limit, self.max_rows)
        warnings: list[str] = []
        if requested_limit > self.max_rows:
            warnings.append(
                f"El límite solicitado ({requested_limit}) supera MAX_RESULT_ROWS="
                f"{self.max_rows}; se aplicó {applied_limit}."
            )

        import sqlglot
        from sqlglot import exp

        try:
            tree = sqlglot.parse_one(sql, read=self.dialect)
        except sqlglot.errors.ParseError as exc:
            return SqlFeedbackApplication(
                sql=sql.strip().rstrip(";"),
                requested_limit=requested_limit,
                warnings=[f"No se pudo aplicar el cambio determinístico de LIMIT: {exc}"],
            )
        previous_limit = self._read_limit(tree)
        tree.set("limit", exp.Limit(expression=exp.Literal.number(applied_limit)))
        normalized = tree.sql(dialect=self.dialect, pretty=True)
        return SqlFeedbackApplication(
            sql=normalized,
            requested_limit=requested_limit,
            applied_limit=applied_limit,
            previous_limit=previous_limit,
            changed=previous_limit != applied_limit,
            warnings=warnings,
        )

    def reconcile_interpretation(
        self,
        interpretation: str,
        application: SqlFeedbackApplication,
    ) -> str:
        if application.applied_limit is None:
            return interpretation

        replacement = f"con un máximo de {application.applied_limit} resultados"
        updated = interpretation
        for pattern in self._INTERPRETATION_LIMIT_PATTERNS:
            if pattern.search(updated):
                return pattern.sub(replacement, updated, count=1)

        suffix = f" La consulta devuelve como máximo {application.applied_limit} resultados."
        return updated.rstrip().rstrip(".") + "." + suffix

    @classmethod
    def extract_requested_limit(cls, feedback: str | None) -> int | None:
        if not feedback:
            return None
        for pattern in cls._LIMIT_PATTERNS:
            match = pattern.search(feedback)
            if match:
                value = re.sub(r"[^0-9]", "", match.group(1))
                if value:
                    parsed = int(value)
                    return parsed if parsed > 0 else None
        return None

    @staticmethod
    def _read_limit(tree: Any) -> int | None:
        from sqlglot import exp

        limit = tree.args.get("limit")
        expression = limit.args.get("expression") if limit is not None else None
        if isinstance(expression, exp.Literal) and expression.is_int:
            return int(expression.this)
        return None
