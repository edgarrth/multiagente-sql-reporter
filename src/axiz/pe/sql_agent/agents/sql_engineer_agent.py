from __future__ import annotations

from axiz.pe.sql_agent.services.agent_skills import AgentSkillSpec
from axiz.pe.sql_agent.services.llm import ModeBoundStructuredLLM, StructuredLLM
from axiz.pe.sql_agent.skills.sql.revision_review import SqlRevisionReviewSkill
from axiz.pe.sql_agent.skills.sql.generation import SqlGenerationSkill


class SqlEngineerAgent:
    """Autonomous SQL engineer with generation, revision, repair, and review modes."""

    def __init__(
        self,
        llm: StructuredLLM,
        skill: AgentSkillSpec,
        *,
        dialect: str,
        max_result_rows: int,
        **_: object,
    ) -> None:
        self.llm = llm
        self.skill = skill
        self._generation = SqlGenerationSkill(
            self._mode(llm, "generate", 2200),
            dialect,
            max_result_rows,
            repair_llm=self._mode(llm, "repair", 1500),
            revision_llm=self._mode(llm, "revise", 2200),
        )
        self._revision_review = SqlRevisionReviewSkill(
            self._mode(llm, "review_revision", 1300),
            dialect=dialect,
        )

    def _mode(self, llm: StructuredLLM, mode: str, max_tokens: int) -> ModeBoundStructuredLLM:
        return ModeBoundStructuredLLM(
            llm,
            operation=mode,
            max_output_tokens=max_tokens,
            system_prefix=self.skill.system_prefix(mode),
        )

    async def generate(self, **kwargs):
        return await self._generation.generate(**kwargs)

    async def review_revision(self, **kwargs):
        return await self._revision_review.validate(**kwargs)

    async def validate(self, **kwargs):
        return await self.review_revision(**kwargs)
