"""Pydantic AI adapter for ADS's grammar-constrained local model.

Pydantic AI owns the commodity turn/tool/retry loop. ADS still owns the action
contract, permission broker, attempted-call budget, evidence semantics, and final
artifact validation. The adapter deliberately exposes no provider or UI extras.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from ads.llm import LLMResponse, ModelProfile, StructuredLLM


class InvestigationCompletion(BaseModel):
    """Internal loop terminator; it is never persisted or rendered."""

    status: Literal["finished", "abandoned"]
    reason: str


@dataclass
class StructuredLLMFunctionModel[ActionT: BaseModel]:
    """Translate one ADS action contract into Pydantic AI model parts.

    The local model continues to decode against the existing action schema. A
    ``call_tool`` action becomes a Pydantic AI function call; terminal actions
    become the agent's typed output tool. Invalid JSON/actions are deliberately
    converted into invalid output calls so Pydantic AI's corrective retry path
    handles them.
    """

    llm: StructuredLLM
    profile: ModelProfile
    action_contract: type[ActionT]
    max_transcript_chars: int
    responses: list[LLMResponse] = field(default_factory=list)
    validation_failures: list[str] = field(default_factory=list)

    def model(self) -> FunctionModel:
        return FunctionModel(self._request, model_name=self.profile.name)

    @property
    def request_count(self) -> int:
        return len(self.responses)

    @property
    def latency_s(self) -> float:
        return round(sum(item.latency_s for item in self.responses), 3)

    @property
    def model_name(self) -> str:
        return self.responses[-1].model if self.responses else self.profile.name

    def _request(
        self,
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        response = self.llm.generate_structured(
            system=info.instructions or "",
            prompt=self._render_prompt(messages),
            json_schema=self.action_contract.model_json_schema(),
            profile=self.profile,
        )
        self.responses.append(response)
        output_tool = info.output_tools[0]
        if response.parsed is None:
            self.validation_failures.append("invalid_json")
            return ModelResponse(parts=[ToolCallPart(output_tool.name, {})])
        try:
            action = self.action_contract.model_validate(response.parsed)
        except ValidationError:
            self.validation_failures.append("invalid_action")
            return ModelResponse(parts=[ToolCallPart(output_tool.name, {})])

        action_name = str(action.action)
        reason = str(action.reason)
        if action_name == "call_tool":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "invoke_tool",
                        {
                            "tool_id": action.tool_id,
                            "arguments": action.arguments,
                            "reason": reason,
                        },
                    )
                ]
            )
        if action_name == "finish":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        output_tool.name,
                        {"status": "finished", "reason": reason},
                    )
                ]
            )
        if action_name == "abandon":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        output_tool.name,
                        {"status": "abandoned", "reason": reason},
                    )
                ]
            )
        self.validation_failures.append("invalid_action")
        return ModelResponse(parts=[ToolCallPart(output_tool.name, {})])

    def _render_prompt(self, messages: list[ModelMessage]) -> str:
        serialized = ModelMessagesTypeAdapter.dump_json(messages).decode("utf-8")
        if len(serialized) <= self.max_transcript_chars:
            return serialized
        # Preserve the initial user context and the newest loop state. The cut is
        # text context for the model, not data that another parser consumes.
        first = ModelMessagesTypeAdapter.dump_json(messages[:1]).decode("utf-8")
        tail_budget = max(self.max_transcript_chars - len(first) - 32, 0)
        return first + "\n...truncated...\n" + serialized[-tail_budget:]


__all__ = ["InvestigationCompletion", "StructuredLLMFunctionModel"]
