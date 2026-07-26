from __future__ import annotations

import json
import re
from datetime import date, datetime
from io import BytesIO
from typing import Any
from uuid import UUID

import xlsxwriter

from axiz.pe.sql_agent.models.contracts import ExcelExportAvailability, QueryResult, RunStatus

_EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_FILE_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ExcelExportTool:
    """Create governed XLSX exports from an already executed SQL result.

    This is deliberately a deterministic tool rather than an LLM agent. It only exports
    persisted query results that already passed HITL, SQL security, cost validation and
    read-only execution.
    """

    mime_type = _EXCEL_MIME

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_rows: int = 5_000,
        allow_truncated: bool = False,
    ) -> None:
        self.enabled = enabled
        self.max_rows = max_rows
        self.allow_truncated = allow_truncated

    def availability(
        self,
        result: QueryResult | None,
        status: RunStatus | str,
    ) -> ExcelExportAvailability:
        normalized_status = status.value if isinstance(status, RunStatus) else str(status)
        if not self.enabled:
            return ExcelExportAvailability(
                available=False,
                reason="La exportación Excel está deshabilitada por configuración.",
            )
        if normalized_status != RunStatus.COMPLETED.value:
            return ExcelExportAvailability(
                available=False,
                reason="Solo se exportan ejecuciones completadas.",
            )
        if result is None or not result.columns or not result.rows:
            return ExcelExportAvailability(
                available=False,
                reason="El resultado no contiene una tabla con filas exportables.",
            )
        if result.row_count > self.max_rows:
            return ExcelExportAvailability(
                available=False,
                reason=(
                    f"El resultado contiene {result.row_count} filas y supera el límite "
                    f"configurado de {self.max_rows}."
                ),
                row_count=result.row_count,
                truncated=result.truncated,
            )
        if result.truncated and not self.allow_truncated:
            return ExcelExportAvailability(
                available=False,
                reason=(
                    "El resultado fue truncado por el límite de consulta. Refina la pregunta "
                    "para evitar descargar un archivo incompleto."
                ),
                row_count=result.row_count,
                truncated=True,
            )
        return ExcelExportAvailability(
            available=True,
            row_count=result.row_count,
            truncated=result.truncated,
        )

    def build(
        self,
        *,
        result: QueryResult,
        run_id: UUID | str,
        question: str,
        sql: str,
        domain: str | None,
        generated_at: datetime | None = None,
    ) -> bytes:
        decision = self.availability(result, RunStatus.COMPLETED)
        if not decision.available:
            raise ValueError(decision.reason or "El resultado no es exportable.")

        output = BytesIO()
        workbook = xlsxwriter.Workbook(
            output,
            {
                "in_memory": True,
                "strings_to_formulas": False,
                "strings_to_urls": False,
                "remove_timezone": True,
            },
        )
        workbook.set_properties(
            {
                "title": "Axiz SQL Agent - Resultado",
                "subject": question[:255],
                "author": "Axiz.pe",
                "company": "Axiz.pe",
                "comments": "Exportación gobernada de un resultado SQL aprobado.",
            }
        )

        header_format = workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#1F4E78",
                "border": 1,
                "align": "center",
                "valign": "vcenter",
            }
        )
        text_format = workbook.add_format({"valign": "top"})
        wrapped_format = workbook.add_format({"text_wrap": True, "valign": "top"})
        int_format = workbook.add_format({"num_format": "#,##0", "valign": "top"})
        number_format = workbook.add_format({"num_format": "#,##0.00", "valign": "top"})
        date_format = workbook.add_format({"num_format": "yyyy-mm-dd", "valign": "top"})
        datetime_format = workbook.add_format(
            {"num_format": "yyyy-mm-dd hh:mm:ss", "valign": "top"}
        )
        metadata_label = workbook.add_format(
            {"bold": True, "bg_color": "#D9EAF7", "border": 1, "valign": "top"}
        )
        metadata_value = workbook.add_format(
            {"border": 1, "text_wrap": True, "valign": "top"}
        )
        note_format = workbook.add_format(
            {"italic": True, "font_color": "#666666", "text_wrap": True}
        )

        worksheet = workbook.add_worksheet("Resultados")
        worksheet.freeze_panes(1, 0)
        worksheet.set_row(0, 24)

        for column_index, column_name in enumerate(result.columns):
            worksheet.write(0, column_index, self._safe_text(column_name), header_format)

        widths = [len(str(column)) for column in result.columns]
        for row_index, row in enumerate(result.rows, start=1):
            for column_index, column_name in enumerate(result.columns):
                value = row.get(column_name)
                normalized, cell_format = self._normalize_value(
                    value,
                    text_format=text_format,
                    wrapped_format=wrapped_format,
                    int_format=int_format,
                    number_format=number_format,
                    date_format=date_format,
                    datetime_format=datetime_format,
                )
                worksheet.write(row_index, column_index, normalized, cell_format)
                widths[column_index] = max(
                    widths[column_index],
                    min(40, len(str(normalized)) if normalized is not None else 0),
                )

        last_row = len(result.rows)
        last_column = len(result.columns) - 1
        if last_row > 0 and last_column >= 0:
            worksheet.add_table(
                0,
                0,
                last_row,
                last_column,
                {
                    "name": "AxizQueryResults",
                    "style": "Table Style Medium 2",
                    "columns": [{"header": self._safe_text(c)} for c in result.columns],
                },
            )
        for column_index, width in enumerate(widths):
            worksheet.set_column(column_index, column_index, min(max(width + 2, 10), 42))

        metadata = workbook.add_worksheet("Metadatos")
        metadata.set_column("A:A", 24)
        metadata.set_column("B:B", 90)
        generated = generated_at or datetime.now().astimezone()
        items: list[tuple[str, Any]] = [
            ("Run ID", str(run_id)),
            ("Fecha de exportación", generated.replace(tzinfo=None)),
            ("Dominio", domain or "No especificado"),
            ("Pregunta", self._safe_text(question)),
            ("Filas exportadas", result.row_count),
            ("Resultado truncado", "Sí" if result.truncated else "No"),
            ("Tiempo de ejecución (ms)", result.elapsed_ms),
            ("SQL ejecutado", self._safe_text(sql)),
        ]
        for row_index, (label, value) in enumerate(items):
            metadata.write(row_index, 0, label, metadata_label)
            if isinstance(value, datetime):
                metadata.write_datetime(row_index, 1, value, datetime_format)
            else:
                metadata.write(row_index, 1, value, metadata_value)
        metadata.write(
            len(items) + 1,
            0,
            "Nota",
            metadata_label,
        )
        metadata.write(
            len(items) + 1,
            1,
            (
                "El archivo contiene únicamente el resultado aprobado y ejecutado con el rol "
                "de solo lectura. Los valores que comienzan con =, +, - o @ se neutralizan "
                "para prevenir fórmulas inyectadas en Excel."
            ),
            note_format,
        )

        workbook.close()
        return output.getvalue()


    def build_investigation(
        self,
        *,
        run_id: UUID | str,
        question: str,
        answer: str,
        evidence: list[dict[str, Any]],
        generated_at: datetime | None = None,
    ) -> bytes:
        """Export every verified evidence package from an autonomous investigation."""
        if not self.enabled:
            raise ValueError("La exportación Excel está deshabilitada por configuración.")
        if not evidence:
            raise ValueError("La investigación no contiene evidencia exportable.")
        parsed: list[tuple[dict[str, Any], QueryResult]] = []
        for item in evidence:
            result = QueryResult.model_validate(item.get("result") or {})
            decision = self.availability(result, RunStatus.COMPLETED)
            if not decision.available:
                raise ValueError(
                    f"La evidencia {item.get('evidence_id') or item.get('task_id')} no es exportable: "
                    + str(decision.reason or "resultado inválido")
                )
            parsed.append((item, result))

        output = BytesIO()
        workbook = xlsxwriter.Workbook(
            output,
            {
                "in_memory": True,
                "strings_to_formulas": False,
                "strings_to_urls": False,
                "remove_timezone": True,
            },
        )
        workbook.set_properties(
            {
                "title": "Axiz SQL Agent - Investigación autónoma",
                "subject": question[:255],
                "author": "Axiz.pe",
                "company": "Axiz.pe",
                "comments": "Evidencia SQL aprobada individualmente mediante HITL.",
            }
        )
        header = workbook.add_format(
            {"bold": True, "font_color": "#FFFFFF", "bg_color": "#1F4E78", "border": 1}
        )
        label = workbook.add_format({"bold": True, "bg_color": "#D9EAF7", "border": 1})
        wrapped = workbook.add_format({"text_wrap": True, "valign": "top", "border": 1})
        number = workbook.add_format({"num_format": "#,##0.00", "valign": "top"})
        integer = workbook.add_format({"num_format": "#,##0", "valign": "top"})
        generated = (generated_at or datetime.now().astimezone()).replace(tzinfo=None)

        summary = workbook.add_worksheet("Resumen")
        summary.set_column("A:A", 24)
        summary.set_column("B:B", 100)
        summary_items = [
            ("Run ID", str(run_id)),
            ("Fecha", generated.isoformat(sep=" ", timespec="seconds")),
            ("Pregunta", self._safe_text(question)),
            ("Respuesta", self._safe_text(answer)),
            ("Cantidad de evidencias", len(parsed)),
        ]
        for row, (name, value) in enumerate(summary_items):
            summary.write(row, 0, name, label)
            summary.write(row, 1, value, wrapped)
        start = len(summary_items) + 2
        for offset, (item, result) in enumerate(parsed):
            row = start + offset
            summary.write(row, 0, str(item.get("evidence_id") or f"evidence-{offset+1}"), label)
            summary.write(
                row,
                1,
                self._safe_text(item.get("summary") or item.get("interpretation") or ""),
                wrapped,
            )

        used_names: set[str] = {"Resumen", "Metadatos"}
        for index, (item, result) in enumerate(parsed, start=1):
            base = f"Evidencia {index:02d}"
            sheet_name = base[:31]
            suffix = 1
            while sheet_name in used_names:
                suffix += 1
                sheet_name = f"{base[:27]}-{suffix}"[:31]
            used_names.add(sheet_name)
            sheet = workbook.add_worksheet(sheet_name)
            sheet.freeze_panes(1, 0)
            widths = [len(str(column)) for column in result.columns]
            for column_index, column_name in enumerate(result.columns):
                sheet.write(0, column_index, self._safe_text(column_name), header)
            for row_index, row in enumerate(result.rows, start=1):
                for column_index, column_name in enumerate(result.columns):
                    value = row.get(column_name)
                    if isinstance(value, bool):
                        fmt = wrapped
                    elif isinstance(value, int):
                        fmt = integer
                    elif isinstance(value, float):
                        fmt = number
                    else:
                        value = self._safe_text(
                            json.dumps(value, ensure_ascii=False, default=str)
                            if isinstance(value, (dict, list, tuple, set))
                            else value
                        )
                        fmt = wrapped
                    sheet.write(row_index, column_index, value, fmt)
                    widths[column_index] = max(widths[column_index], min(40, len(str(value))))
            if result.rows and result.columns:
                sheet.add_table(
                    0, 0, len(result.rows), len(result.columns) - 1,
                    {
                        "name": f"AxizEvidence{index}",
                        "style": "Table Style Medium 2",
                        "columns": [{"header": self._safe_text(c)} for c in result.columns],
                    },
                )
            for column_index, width in enumerate(widths):
                sheet.set_column(column_index, column_index, min(max(width + 2, 10), 42))

        metadata = workbook.add_worksheet("Metadatos")
        metadata.set_column("A:A", 24)
        metadata.set_column("B:B", 100)
        row = 0
        for index, (item, result) in enumerate(parsed, start=1):
            records = [
                ("Evidencia", item.get("evidence_id") or index),
                ("Tarea", item.get("task_id") or ""),
                ("Especialista", str(item.get("specialist") or "")),
                ("Dominio", item.get("domain") or ""),
                ("Filas", result.row_count),
                ("Tiempo SQL (ms)", result.elapsed_ms),
                ("SQL", item.get("sql") or ""),
            ]
            for name, value in records:
                metadata.write(row, 0, name, label)
                metadata.write(row, 1, self._safe_text(value), wrapped)
                row += 1
            row += 1
        workbook.close()
        return output.getvalue()

    @staticmethod
    def filename(question: str, run_id: UUID | str) -> str:
        base = " ".join(question.strip().split())[:60] or "resultado-sql"
        base = _FILE_SAFE.sub("-", base).strip("-._").lower() or "resultado-sql"
        return f"{base}-{str(run_id)[:8]}.xlsx"

    @staticmethod
    def _safe_text(value: Any) -> str:
        text = str(value if value is not None else "")
        return text

    def _normalize_value(
        self,
        value: Any,
        *,
        text_format: Any,
        wrapped_format: Any,
        int_format: Any,
        number_format: Any,
        date_format: Any,
        datetime_format: Any,
    ) -> tuple[Any, Any]:
        if value is None:
            return None, text_format
        if isinstance(value, bool):
            return value, text_format
        if isinstance(value, int):
            return value, int_format
        if isinstance(value, float):
            return value, number_format
        if isinstance(value, datetime):
            return value.replace(tzinfo=None), datetime_format
        if isinstance(value, date):
            return value, date_format
        if isinstance(value, (dict, list, tuple, set)):
            return self._safe_text(json.dumps(value, ensure_ascii=False, default=str)), wrapped_format
        text = self._safe_text(value)
        return text, wrapped_format if len(text) > 80 or "\n" in text else text_format
