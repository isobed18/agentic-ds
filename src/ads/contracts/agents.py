"""Durable, row-free evidence about agent execution and review panels."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field

from ads.contracts.base import Artifact, ArtifactType, FrozenModel


class AgentMemberAudit(FrozenModel):
    """Safe execution facts for one panel member; prompts and responses are excluded."""

    member: int = Field(ge=1)
    model: str
    attempts: int = Field(ge=1)
    accepted: bool
    validation_failures: list[str] = Field(default_factory=list)
    repairs: list[str] = Field(default_factory=list)
    latency_s: float = Field(ge=0.0)


class AgentAudit(Artifact):
    """What constrained and checked an agent-backed stage."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.AGENT_AUDIT
    schema_version: ClassVar[str] = "1"

    stage_id: str
    agent_id: str
    output_contract: str
    panel_size: int = Field(ge=1, le=5)
    valid_members: int = Field(ge=0)
    agreement: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Fraction of the panel that reached the same decision.",
    )
    verbatim_agreement: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction that produced byte-identical contracts. Never gated on. "
            "Sitting far below `agreement` means the panel agreed on the "
            "decision and differed only in how it was worded."
        ),
    )
    allowed_tools: list[str] = Field(default_factory=list)
    evidence_tools: list[str] = Field(default_factory=list)
    validator_count: int = Field(ge=0)
    pydantic_contract_enforced: bool = True
    raw_rows_shared: bool = False
    members: list[AgentMemberAudit] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "panel_size": self.panel_size,
            "valid_members": self.valid_members,
            "agreement": self.agreement,
            "output_contract": self.output_contract,
            "validator_count": self.validator_count,
            "tool_count": len(self.evidence_tools),
            "pydantic_validated": self.pydantic_contract_enforced,
            "raw_rows_shared": self.raw_rows_shared,
        }


__all__ = ["AgentAudit", "AgentMemberAudit"]
