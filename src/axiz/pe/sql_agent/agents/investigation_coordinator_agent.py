from __future__ import annotations

from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache
from axiz.pe.sql_agent.services.agent_skills import AgentSkillSpec
from axiz.pe.sql_agent.services.llm import ModeBoundStructuredLLM, StructuredLLM
from axiz.pe.sql_agent.skills.coordinator.complexity_routing import ComplexityRoutingSkill
from axiz.pe.sql_agent.skills.coordinator.context_resolution import ContextResolutionSkill
from axiz.pe.sql_agent.skills.coordinator.conversation_memory import ConversationMemorySkill
from axiz.pe.sql_agent.skills.coordinator.intent_routing import IntentRoutingSkill
from axiz.pe.sql_agent.skills.coordinator.investigation_planning import InvestigationPlanningSkill
from axiz.pe.sql_agent.skills.coordinator.supervision import SupervisionSkill


class InvestigationCoordinatorAgent:
    """The single coordinator identity for context, routing, planning and supervision."""

    def __init__(
        self,
        llm: StructuredLLM,
        cache: AgentResponseCache | None,
        skill: AgentSkillSpec,
    ) -> None:
        self.llm = llm
        self.skill = skill
        context_llm = self._mode(llm, "context", 700)
        route_llm = self._mode(llm, "route", 700)
        plan_llm = self._mode(llm, "plan", 2200)
        supervise_llm = self._mode(llm, "supervise", 1800)
        synthesize_llm = self._mode(llm, "synthesize", 2200)
        conversation_llm = self._mode(llm, "conversation", 900)
        self._context = ContextResolutionSkill(context_llm, cache)
        self._intent = IntentRoutingSkill(route_llm, cache)
        self._conversation = ConversationMemorySkill(conversation_llm)
        self._router = ComplexityRoutingSkill(route_llm, cache)
        self._planner = InvestigationPlanningSkill(plan_llm, cache)
        self._supervision = SupervisionSkill(supervise_llm, synthesize_llm)

    def _mode(self, llm: StructuredLLM, mode: str, max_tokens: int) -> ModeBoundStructuredLLM:
        return ModeBoundStructuredLLM(
            llm,
            operation=mode,
            max_output_tokens=max_tokens,
            system_prefix=self.skill.system_prefix(mode),
        )

    async def resolve(self, **kwargs):
        return await self._context.resolve(**kwargs)

    async def classify(self, *args, **kwargs):
        return await self._intent.classify(*args, **kwargs)

    async def answer(self, **kwargs):
        return await self._conversation.answer(**kwargs)

    async def route(self, **kwargs):
        return await self._router.route(**kwargs)

    async def plan(self, **kwargs):
        return await self._planner.plan(**kwargs)

    async def decide(self, **kwargs):
        return await self._supervision.decide(**kwargs)

    async def synthesize(self, **kwargs):
        return await self._supervision.synthesize(**kwargs)
