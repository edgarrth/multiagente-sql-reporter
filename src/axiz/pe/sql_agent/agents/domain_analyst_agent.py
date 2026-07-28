from __future__ import annotations

from axiz.pe.sql_agent.services.agent_skills import AgentSkillSpec
from axiz.pe.sql_agent.services.llm import ModeBoundStructuredLLM, StructuredLLM
from axiz.pe.sql_agent.services.specialist_registry import SpecialistProfile
from axiz.pe.sql_agent.skills.domain_analysis import DomainAnalysisSkill


class DomainAnalystAgent:
    """One agent identity instantiated with many domain capability profiles."""

    def __init__(
        self,
        profile: SpecialistProfile,
        llm: StructuredLLM,
        skill: AgentSkillSpec,
    ) -> None:
        self.profile = profile
        self.skill = skill
        prepare_llm = ModeBoundStructuredLLM(
            llm,
            operation="prepare",
            max_output_tokens=1100,
            system_prefix=skill.system_prefix("prepare"),
        )
        review_llm = ModeBoundStructuredLLM(
            llm,
            operation="review",
            max_output_tokens=1200,
            system_prefix=skill.system_prefix("review"),
        )
        # The operation skill remains domain-neutral; the profile supplies personality/context.
        self._prepare_skill = DomainAnalysisSkill(profile, prepare_llm)
        self._review_skill = DomainAnalysisSkill(profile, review_llm)

    async def prepare(self, **kwargs):
        return await self._prepare_skill.prepare(**kwargs)

    async def review_proposal(self, **kwargs):
        return await self._review_skill.review_proposal(**kwargs)
