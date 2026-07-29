from __future__ import annotations

"""Generic SQL structure analysis backed by SQLGlot.

This module understands SQL syntax only. It deliberately contains no business vocabulary,
mandatory-clause policy, natural-language heuristics, or feedback taxonomy.
"""

from typing import Any


class SqlAstAnalyzer:
    def __init__(self, *, dialect: str = "postgres") -> None:
        self.dialect = dialect

    def parse(self, sql: str) -> Any:
        import sqlglot

        return sqlglot.parse_one(sql, read=self.dialect)

    def sources(self, tree: Any) -> list[str]:
        from sqlglot import exp

        cte_names = {
            str(cte.alias_or_name).strip().lower()
            for cte in tree.find_all(exp.CTE)
            if cte.alias_or_name
        }
        result: list[str] = []
        for table in tree.find_all(exp.Table):
            if not table.catalog and not table.db and table.name.lower() in cte_names:
                continue
            source = ".".join(
                str(value)
                for value in (table.catalog, table.db, table.name)
                if value
            )
            if source and source not in result:
                result.append(source)
        return result

    @staticmethod
    def root_select(tree: Any) -> Any | None:
        from sqlglot import exp

        if isinstance(tree, exp.Select):
            return tree
        if isinstance(tree, exp.With):
            if isinstance(tree.this, exp.Select):
                return tree.this
        return next(tree.find_all(exp.Select), None)
