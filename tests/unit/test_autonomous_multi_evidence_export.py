from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

from axiz.pe.sql_agent.tools.excel_export import ExcelExportTool


def test_autonomous_investigation_exports_summary_all_evidence_and_metadata() -> None:
    tool = ExcelExportTool(enabled=True, max_rows=100)
    content = tool.build_investigation(
        run_id=uuid4(),
        question="Investiga la variación",
        answer="La evidencia fue verificada.",
        evidence=[
            {
                "evidence_id": "evidence-1",
                "task_id": "task-a",
                "specialist": "acquiring",
                "domain": "acquiring",
                "sql": "SELECT channel, amount FROM semantic.v_daily_payment_metrics LIMIT 2",
                "summary": "Evidencia por canal",
                "result": {
                    "columns": ["channel", "amount"],
                    "rows": [
                        {"channel": "web", "amount": 10.0},
                        {"channel": "pos", "amount": 20.0},
                    ],
                    "row_count": 2,
                    "elapsed_ms": 8.0,
                    "truncated": False,
                },
            },
            {
                "evidence_id": "evidence-2",
                "task_id": "task-b",
                "specialist": "temporal",
                "domain": "acquiring",
                "sql": "SELECT metric_date, amount FROM semantic.v_daily_payment_metrics LIMIT 1",
                "summary": "Evidencia temporal",
                "result": {
                    "columns": ["metric_date", "amount"],
                    "rows": [{"metric_date": "2026-07-01", "amount": 15.0}],
                    "row_count": 1,
                    "elapsed_ms": 5.0,
                    "truncated": False,
                },
            },
        ],
    )

    assert content.startswith(b"PK")
    with ZipFile(BytesIO(content)) as workbook:
        workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
        assert "Resumen" in workbook_xml
        assert "Evidencia 01" in workbook_xml
        assert "Evidencia 02" in workbook_xml
        assert "Metadatos" in workbook_xml
        shared_strings = workbook.read("xl/sharedStrings.xml").decode("utf-8")
        assert "evidence-1" in shared_strings
        assert "evidence-2" in shared_strings
