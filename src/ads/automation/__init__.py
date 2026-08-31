"""Graph-native automation component catalog and safe graph transformations."""

from ads.automation.catalog import (
    AutomationComponentDefinition,
    PlannerGraphEditRejected,
    add_problem_branches,
    apply_planner_graph_operations,
    automation_component_catalog,
    instantiate_component,
)
from ads.automation.compiler import compile_automation_plan
from ads.automation.runner import (
    AutomationRunner,
    AutomationRunResult,
    ExecutorRegistry,
    NodeExecutionContext,
    NodeExecutionResult,
)
from ads.automation.store import AutomationRevisionConflict, AutomationStore

__all__ = [
    "AutomationComponentDefinition",
    "add_problem_branches",
    "PlannerGraphEditRejected",
    "apply_planner_graph_operations",
    "automation_component_catalog",
    "compile_automation_plan",
    "AutomationRunResult",
    "AutomationRunner",
    "AutomationRevisionConflict",
    "AutomationStore",
    "ExecutorRegistry",
    "NodeExecutionContext",
    "NodeExecutionResult",
    "instantiate_component",
]
