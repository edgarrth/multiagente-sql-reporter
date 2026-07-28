#!/usr/bin/env python3
"""Validate internal package imports without importing optional runtime dependencies.

This catches packaging/startup regressions such as moving a symbol to another module while
leaving an old ``from ... import ...`` statement behind. The check is intentionally based on the
source tree so it runs in lightweight CI jobs that do not install LangGraph or PostgreSQL drivers.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

PACKAGE_PREFIX = "axiz."


@dataclass(frozen=True)
class ImportIssue:
    source: Path
    line: int
    module: str
    symbol: str

    def render(self) -> str:
        return f"{self.source}:{self.line}: cannot resolve {self.module}.{self.symbol}"


def _module_file(source_root: Path, module: str) -> Path | None:
    base = source_root.joinpath(*module.split("."))
    module_file = base.with_suffix(".py")
    if module_file.is_file():
        return module_file
    package_file = base / "__init__.py"
    if package_file.is_file():
        return package_file
    return None


def _top_level_names(module_file: Path) -> set[str]:
    tree = ast.parse(module_file.read_text(encoding="utf-8"), filename=str(module_file))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", maxsplit=1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
    return names


def find_internal_import_issues(source_root: Path) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for source in source_root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module
            if not module or not module.startswith(PACKAGE_PREFIX):
                continue
            module_file = _module_file(source_root, module)
            if module_file is None:
                issues.append(ImportIssue(source, node.lineno, module, "<module>"))
                continue
            available_names = _top_level_names(module_file)
            module_base = source_root.joinpath(*module.split("."))
            for alias in node.names:
                if alias.name == "*" or alias.name in available_names:
                    continue
                # ``from package import submodule`` is valid even when __init__.py does not
                # explicitly re-export the submodule.
                if (module_base / f"{alias.name}.py").is_file() or (
                    module_base / alias.name / "__init__.py"
                ).is_file():
                    continue
                issues.append(ImportIssue(source, node.lineno, module, alias.name))
    return sorted(issues, key=lambda issue: (str(issue.source), issue.line, issue.symbol))


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    source_root = project_root / "src"
    issues = find_internal_import_issues(source_root)
    if issues:
        print("Internal import validation failed:")
        for issue in issues:
            print(f"- {issue.render()}")
        return 1
    print("Internal import validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
