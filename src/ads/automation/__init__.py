"""Graph-native automation component catalog and safe graph transformations."""

from ads.automation.catalog import (
    AutomationComponentDefinition,
    add_problem_branches,
    apply_planner_graph_operations,
    automation_component_catalog,
    instantiate_component,
)
from ads.automation.compiler import compile_automation_plan

__all__ = [
    "AutomationComponentDefinition",
    "add_problem_branches",
    "apply_planner_graph_operations",
    "automation_component_catalog",
    "compile_automation_plan",
    "instantiate_component",
]
