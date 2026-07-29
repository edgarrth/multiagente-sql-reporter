from __future__ import annotations

from axiz.pe.sql_agent.services.agent_skills import AgentSkillSpec
from axiz.pe.sql_agent.services.llm import ModeBoundStructuredLLM, StructuredLLM
from axiz.pe.sql_agent.skills.evidence.critique import EvidenceCritiqueSkill
from axiz.pe.sql_agent.skills.evidence.explanation import EvidenceExplanationSkill
from axiz.pe.sql_agent.skills.evidence.verification import EvidenceVerificationSkill
from axiz.pe.sql_agent.tools.chart_builder import ChartBuilderTool


class EvidenceReviewerAgent:
    """Single evidence identity for verification, critique and grounded explanation."""

    def __init__(
        self,
        llm: StructuredLLM,
        charts: ChartBuilderTool,
        skill: AgentSkillSpec,
    ) -> None:
        self.llm = llm
        self.skill = skill
        verify_llm = self._mode(llm, "verify", 900)
        explain_llm = self._mode(llm, "explain", 1500)
        catalog_llm = self._mode(llm, "catalog_answer", 1000)
        critic_llm = self._mode(llm, "criticize", 1600)
        self._verification = EvidenceVerificationSkill(verify_llm)
        self._explanation = EvidenceExplanationSkill(explain_llm, catalog_llm, charts)
        self._critique = EvidenceCritiqueSkill(critic_llm)


    def _mode(self, llm: StructuredLLM, mode: str, max_tokens: int) -> ModeBoundStructuredLLM:
        return ModeBoundStructuredLLM(
            llm,
            operation=mode,
            max_output_tokens=max_tokens,
            system_prefix=self.skill.system_prefix(mode),
        )

    async def verify(self, **kwargs):
        return await self._verification.verify(**kwargs)

    async def explain(self, **kwargs):
        return await self._explanation.explain(**kwargs)

    async def answer_catalog_question(self, **kwargs):
        return await self._explanation.answer_catalog_question(**kwargs)

    async def review(self, **kwargs):
        return await self._critique.review(**kwargs)
