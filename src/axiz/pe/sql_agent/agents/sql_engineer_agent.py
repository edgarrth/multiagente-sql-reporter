from __future__ import annotations

from axiz.pe.sql_agent.services.agent_skills import AgentSkillSpec
from axiz.pe.sql_agent.services.llm import ModeBoundStructuredLLM, StructuredLLM
from axiz.pe.sql_agent.skills.sql.compliance import FeedbackComplianceSkill
from axiz.pe.sql_agent.skills.sql.feedback_planning import FeedbackPlanningSkill
from axiz.pe.sql_agent.skills.sql.generation import SqlGenerationSkill
from axiz.pe.sql_agent.tools.sql_feedback_plan import SqlFeedbackPlanValidator


class SqlEngineerAgent:
    """Single SQL agent identity with generation, feedback, revision and repair skills."""

    def __init__(
        self,
        llm: StructuredLLM,
        skill: AgentSkillSpec,
        *,
        dialect: str,
        max_result_rows: int,
        feedback_plan_validator: SqlFeedbackPlanValidator,
    ) -> None:
        self.llm = llm
        self.skill = skill
        generate_llm = self._mode(llm, "generate", 2200)
        repair_llm = self._mode(llm, "repair", 1400)
        revision_llm = self._mode(llm, "revise", 1800)
        feedback_llm = self._mode(llm, "interpret_feedback", 1300)
        compliance_llm = self._mode(llm, "feedback_compliance", 1200)
        self._generation = SqlGenerationSkill(
            generate_llm,
            dialect,
            max_result_rows,
            repair_llm=repair_llm,
            revision_llm=revision_llm,
        )
        self._feedback = FeedbackPlanningSkill(
            feedback_llm,
            max_result_rows,
            feedback_plan_validator,
            dialect=dialect,
        )
        self._compliance = FeedbackComplianceSkill(compliance_llm)

        # Compatibility views are the same agent, not additional agent identities.
        self.generator = self
        self.feedback_interpreter = self
        self.feedback_compliance = self

    def _mode(self, llm: StructuredLLM, mode: str, max_tokens: int) -> ModeBoundStructuredLLM:
        return ModeBoundStructuredLLM(
            llm,
            operation=mode,
            max_output_tokens=max_tokens,
            system_prefix=self.skill.system_prefix(mode),
        )

    async def generate(self, **kwargs):
        return await self._generation.generate(**kwargs)

    async def interpret(self, **kwargs):
        return await self._feedback.interpret(**kwargs)

    async def validate(self, **kwargs):
        return await self._compliance.validate(**kwargs)
