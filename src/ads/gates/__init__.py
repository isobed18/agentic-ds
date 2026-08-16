"""The Gate Evaluator: deterministic decisions about when a human is needed.

The LLM is a sensor; this package is the actuator. Nothing here calls a model.
"""

from ads.gates.evaluator import build_human_prompt, evaluate_gate
from ads.gates.policy import (
    DEFAULT_POLICY_PATH,
    GatePolicy,
    GateThresholds,
    StageHistory,
    StageSpec,
)
from ads.gates.rules import RULES, Rule, RuleContext, RuleOutcome, Tier, applicable_rules

__all__ = [
    "DEFAULT_POLICY_PATH",
    "RULES",
    "GatePolicy",
    "GateThresholds",
    "Rule",
    "RuleContext",
    "RuleOutcome",
    "StageHistory",
    "StageSpec",
    "Tier",
    "applicable_rules",
    "build_human_prompt",
    "evaluate_gate",
]
