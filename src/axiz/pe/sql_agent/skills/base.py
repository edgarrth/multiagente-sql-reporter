from __future__ import annotations

from abc import ABC

from pydantic import BaseModel

from axiz.pe.sql_agent.services.agent_skills import AgentSkillSpec
from axiz.pe.sql_agent.services.llm import StructuredLLM


class LLMSkill[TIn: BaseModel, TOut: BaseModel](ABC):
    """Typed LLM skill for simple mode-to-contract calls.

    Existing skills with custom caching, retries, repair context, or deterministic pre/post
    processing can keep their local implementation and migrate to this base class gradually.
    """

    mode: str
    input_model: type[TIn]
    output_model: type[TOut]

    def __init__(self, llm: StructuredLLM, spec: AgentSkillSpec) -> None:
        self.llm = llm
        self.spec = spec
        spec.assert_mode_contracts(self.mode, self.input_model, self.output_model)

    async def execute(self, invocation: TIn) -> TOut:
        return await self.llm.parse(
            system=self.spec.system_prefix(self.mode),
            user=invocation.model_dump_json(),
            response_model=self.output_model,
        )
