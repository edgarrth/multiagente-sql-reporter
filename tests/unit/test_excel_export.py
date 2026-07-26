from __future__ import annotations

from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

from axiz.pe.sql_agent.models.contracts import QueryResult, RunStatus
from axiz.pe.sql_agent.tools.excel_export import ExcelExportTool


def _result(*, truncated: bool = False, rows: list[dict] | None = None) -> QueryResult:
    actual_rows = rows if rows is not None else [
        {"merchant": "Comercio Uno", "amount": 1234.5, "formula_like": "=2+2"},
        {"merchant": "Comercio Dos", "amount": 900.0, "formula_like": "@SUM(A1:A2)"},
    ]
    return QueryResult(
        columns=["merchant", "amount", "formula_like"],
        rows=actual_rows,
        row_count=len(actual_rows),
        elapsed_ms=12.5,
        truncated=truncated,
    )


def test_excel_export_is_available_only_for_completed_non_empty_results() -> None:
    tool = ExcelExportTool(enabled=True, max_rows=100, allow_truncated=False)

    assert tool.availability(_result(), RunStatus.COMPLETED).available is True
    assert tool.availability(_result(), RunStatus.FAILED).available is False

    empty = QueryResult(columns=[], rows=[], row_count=0, elapsed_ms=1, truncated=False)
    decision = tool.availability(empty, RunStatus.COMPLETED)
    assert decision.available is False
    assert "no contiene" in (decision.reason or "")


def test_truncated_results_are_not_exported_by_default() -> None:
    tool = ExcelExportTool(enabled=True, max_rows=100, allow_truncated=False)
    decision = tool.availability(_result(truncated=True), RunStatus.COMPLETED)
    assert decision.available is False
    assert "truncado" in (decision.reason or "")


def test_excel_export_builds_xlsx_with_results_and_metadata() -> None:
    tool = ExcelExportTool(enabled=True, max_rows=100, allow_truncated=False)
    content = tool.build(
        result=_result(),
        run_id=uuid4(),
        question="Facturación por comercio",
        sql="SELECT merchant, amount FROM semantic.v_merchant_performance",
        domain="acquiring",
    )

    assert content.startswith(b"PK")
    with ZipFile(BytesIO(content)) as archive:
        names = set(archive.namelist())
        assert "xl/workbook.xml" in names
        assert "xl/worksheets/sheet1.xml" in names
        assert "xl/worksheets/sheet2.xml" in names
        shared_strings = archive.read("xl/sharedStrings.xml").decode("utf-8")
        assert "Resultados" not in shared_strings  # sheet name lives in workbook metadata
        assert "Facturación por comercio" in shared_strings
        assert "=2+2" in shared_strings
        assert "@SUM(A1:A2)" in shared_strings
        worksheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "<f>" not in worksheet_xml


def test_filename_is_safe_and_stable() -> None:
    run_id = uuid4()
    filename = ExcelExportTool.filename("Ventas / MCC: julio?", run_id)
    assert filename.endswith(".xlsx")
    assert "/" not in filename
    assert "?" not in filename
    assert str(run_id)[:8] in filename
