from __future__ import annotations

from axiz.pe.sql_agent.models.contracts import ApprovalDecision, HumanFeedbackRequest


def feedback_content(feedback: HumanFeedbackRequest) -> str:
    if feedback.decision == ApprovalDecision.APPROVE:
        return "Aprobé la consulta SQL para su ejecución."
    if feedback.decision == ApprovalDecision.REJECT:
        suffix = f" Motivo: {feedback.comment.strip()}" if feedback.comment else ""
        return "Rechacé la consulta SQL." + suffix
    return f"Solicité cambios en la consulta SQL: {feedback.comment.strip()}"
