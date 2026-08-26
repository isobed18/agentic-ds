"""Run a real staging + compiled fully-auto graph to completion."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ads.api import ControlPlane
from ads.contracts.base import ArtifactType
from ads.llm import LLMResponse, ModelProfile
from ads.store import ArtifactStore


class ClaudeCliLLM:
    """Test-only StructuredLLM adapter using Claude Code subscription auth."""

    @staticmethod
    def _executable() -> str:
        """Resolve the native binary so Windows never reparses JSON via claude.cmd."""
        npm_wrapper = shutil.which("claude.cmd")
        if npm_wrapper:
            native = (
                Path(npm_wrapper).parent
                / "node_modules"
                / "@anthropic-ai"
                / "claude-code"
                / "bin"
                / "claude.exe"
            )
            if native.is_file():
                return str(native)
        executable = shutil.which("claude")
        if executable:
            return executable
        raise FileNotFoundError("Claude CLI is not available")

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict,
        profile: ModelProfile,
    ) -> LLMResponse:
        del profile
        started = time.perf_counter()
        test_system = (
            system
            + "\n\nTEST EXECUTION CONSTRAINT: minimize deliberation and tool turns. "
            "Use the supplied aggregate evidence, return one valid typed action, and when "
            "a deterministic trial is required, trial a complete plan immediately and then "
            "submit those exact tested semantics on the next action."
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", encoding="utf-8", delete=False
        ) as stream:
            stream.write(test_system)
            system_path = stream.name
        environment = dict(os.environ)
        environment.pop("ANTHROPIC_API_KEY", None)
        print(
            json.dumps(
                {
                    "claude_cli_request": {
                        "prompt_chars": len(prompt),
                        "system_chars": len(test_system),
                        "schema_chars": len(json.dumps(json_schema)),
                    }
                }
            ),
            flush=True,
        )
        try:
            completed = subprocess.run(
                [
                    self._executable(),
                    "-p",
                    "--model",
                    "haiku",
                    "--effort",
                    "low",
                    "--tools",
                    "",
                    "--max-turns",
                    "2",
                    "--no-session-persistence",
                    "--output-format",
                    "json",
                    "--json-schema",
                    json.dumps(json_schema, separators=(",", ":")),
                    "--system-prompt-file",
                    system_path,
                ],
                input=prompt,
                text=True,
                capture_output=True,
                check=False,
                timeout=300,
                env=environment,
            )
            if completed.returncode != 0:
                print(
                    json.dumps(
                        {
                            "claude_cli_error": completed.stderr[-2_000:],
                            "claude_cli_output": completed.stdout[-2_000:],
                            "returncode": completed.returncode,
                        }
                    ),
                    flush=True,
                )
                raise RuntimeError("Claude CLI structured completion failed")
        finally:
            Path(system_path).unlink(missing_ok=True)
        envelope = json.loads(completed.stdout)
        parsed = envelope.get("structured_output")
        text = json.dumps(parsed, ensure_ascii=False) if isinstance(parsed, dict) else ""
        return LLMResponse(
            text=text,
            model="claude-cli-haiku-test-only",
            latency_s=round(time.perf_counter() - started, 3),
            prompt_tokens=int(envelope.get("usage", {}).get("input_tokens", 0)),
            completion_tokens=int(envelope.get("usage", {}).get("output_tokens", 0)),
            parsed=parsed if isinstance(parsed, dict) else None,
            parse_error=None if isinstance(parsed, dict) else "Claude CLI returned no object",
            metadata={"auth": "claude.ai subscription", "api_key": False},
        )


class FastAuditLLM:
    """Deterministic structured responses for a seconds-long orchestration audit.

    This exercises the real agents, tools, gates, graph compiler, estimators and
    artifact store. It intentionally tests orchestration rather than model quality.
    """

    def __init__(self) -> None:
        plan = {
            "base_table": "physicians__physician_master",
            "base_grain": ["physician_id"],
            "grain_description": "One row per physician.",
            "aggregations": [],
            "joins": [
                {
                    "left_table": "physicians__physician_master",
                    "right_table": "physicians__compensation",
                    "left_columns": ["physician_id"],
                    "right_columns": ["physician_id"],
                    "how": "left",
                    "rationale": "Measured one-to-one physician relationship.",
                }
            ],
            "warnings": [],
        }
        self._schema_actions = iter(
            [
                {
                    "action": "call_tool",
                    "tool_id": "candidate_keys",
                    "arguments": {"table": "physicians__physician_master"},
                    "reason": "Verify the proposed base grain.",
                },
                {
                    "action": "call_tool",
                    "tool_id": "join_overlap",
                    "arguments": {
                        "from_table": "physicians__compensation",
                        "from_column": "physician_id",
                        "to_table": "physicians__physician_master",
                        "to_column": "physician_id",
                    },
                    "reason": "Verify join support.",
                },
                {
                    "action": "call_tool",
                    "tool_id": "trial_integration_plan",
                    "arguments": {"plan": plan},
                    "reason": "Run the exact integration plan.",
                },
                {
                    "action": "submit_plan",
                    "plan": plan,
                    "reason": "Submit the plan that passed its deterministic trial.",
                },
            ]
        )

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict,
        profile: ModelProfile,
    ) -> LLMResponse:
        del system, prompt
        title = str(json_schema.get("title") or "")
        if title == "SchemaInvestigationAction":
            parsed = next(self._schema_actions)
        elif title == "InterpretationBatchProposal":
            parsed = {"items": []}
        elif title == "ProblemDiscoveryProposal":
            parsed = {
                "candidates": [
                    {
                        "title": "Predict annual physician compensation",
                        "title_tr": "Yıllık hekim ücretini tahmin et",
                        "task_type": "regression",
                        "target_column": "annual_comp",
                        "business_rationale": "Estimate compensation from pre-outcome attributes.",
                        "business_rationale_tr": "Sonuç öncesi niteliklerden ücreti tahmin et.",
                        "evidence_columns": [
                            "years_experience",
                            "specialty",
                            "city",
                            "hire_date",
                        ],
                        "primary_metric": "rmse",
                    }
                ]
            }
        elif title == "ValidationStrategyProposal":
            parsed = {
                "strategy": "temporal",
                "n_folds": 3,
                "test_size": 0.2,
                "group_column": None,
                "time_column": "hire_date",
                "holdout_cutoff": "2019-01-01",
                "rationale": "Later hires simulate future deployment.",
                "rationale_tr": "Sonraki işe alımlar gelecekteki dağıtımı temsil eder.",
            }
        elif title == "LeakageChallengeAction":
            parsed = {
                "action": "abandon",
                "reason": "No feature-specific recording timestamp exists in this ABT.",
            }
        elif title == "_PlannerChatReply":
            parsed = {
                "reply": "The measured sources are ready for a bounded fully-auto run.",
                "reports": [
                    {
                        "title_en": "Pipeline readiness",
                        "title_tr": "Boru hattı hazırlığı",
                        "summary_en": "The measured schema has an executable join plan.",
                        "summary_tr": (
                            "Ölçülen şema çalıştırılabilir bir birleştirme planına sahip."
                        ),
                        "findings": [
                            {
                                "en": "Use physician_id as the preserved entity grain.",
                                "tr": "Korunan varlık tanesi olarak physician_id kullanın.",
                            }
                        ],
                        "verification_questions": [],
                    }
                ],
                "configuration_patch": {"candidate_limit": 1, "n_folds": 3},
                "stage_directives": {"training": ["Keep model search bounded."]},
                "max_retries_by_stage": {"training": 2},
                "plan_rationale": [
                    {
                        "en": "A bounded baseline is appropriate for this audit.",
                        "tr": "Bu denetim için sınırlı bir temel model uygundur.",
                    }
                ],
            }
        else:
            raise AssertionError(f"Fast audit has no response for schema {title!r}")
        return LLMResponse(
            text=json.dumps(parsed, ensure_ascii=False),
            model="deterministic-e2e-audit",
            latency_s=0.0,
            parsed=parsed,
            metadata={"network": False, "purpose": "orchestration audit"},
        )


def wait_for(plane: ControlPlane, run_id: str, terminal: set[str], timeout: int) -> dict:
    started = time.monotonic()
    last: tuple[str, str | None] | None = None
    while time.monotonic() - started < timeout:
        progress = plane.progress(run_id)
        current = (str(progress.get("status")), progress.get("current_stage"))
        if current != last:
            print(
                json.dumps(
                    {"run_id": run_id, "status": current[0], "stage": current[1]}
                ),
                flush=True,
            )
            last = current
        if current[0] == "awaiting_human":
            pending = progress.get("pending_question") or {}
            prompt = pending.get("human_prompt") or {}
            options = prompt.get("options") or []
            offered = {str(item.get("option_id")) for item in options}
            decision = "approve" if "approve" in offered else next(
                (
                    str(item.get("option_id"))
                    for item in options
                    if item.get("recommended")
                ),
                str(options[0].get("option_id")) if options else "",
            )
            if not decision:
                raise RuntimeError("human gate exposed no decision options")
            print(
                json.dumps(
                    {
                        "run_id": run_id,
                        "human_decision": decision,
                        "stage": pending.get("stage_id"),
                    }
                ),
                flush=True,
            )
            plane.answer_run(
                run_id,
                decision=decision,
                instructions=["Continue this test run using the available artifacts."],
            )
            time.sleep(1)
            continue
        if current[0] in terminal:
            return progress
        time.sleep(3)
    raise TimeoutError(f"run {run_id} did not reach {sorted(terminal)} in {timeout}s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="sample")
    parser.add_argument("--timeout", type=int, default=2_700)
    parser.add_argument(
        "--llm", choices=("ollama", "claude-cli", "fast-audit"), default="ollama"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    fast_audit = FastAuditLLM() if args.llm == "fast-audit" else None
    plane = ControlPlane(
        store=ArtifactStore(root / "data" / "artifacts"),
        source_roots=(root / "data",),
        upload_root=root / "data" / "uploads",
        llm_factory=(
            (lambda: ClaudeCliLLM())
            if args.llm == "claude-cli"
            else (lambda: fast_audit)
            if fast_audit is not None
            else None
        ),
    )

    staged = plane.stage_run(args.source, reuse_cache=False)
    run_id = staged["run_id"]
    print(json.dumps({"run_id": run_id, "status": staged["status"]}), flush=True)
    progress = wait_for(plane, run_id, {"staged", "failed", "aborted"}, args.timeout)
    if progress["status"] != "staged":
        raise RuntimeError(json.dumps(progress, default=str))

    workspace = plane.staging_workspace(run_id)
    if not workspace.get("recommended_plan"):
        raise RuntimeError("staging completed without a planner recommendation")
    compiled = plane.compile_automation(run_id)
    print(
        json.dumps(
            {
                "execution_plan": compiled["artifact_id"],
                "nodes": len(compiled["plan"]["nodes"]),
                "pause_after": compiled["plan"]["pause_after_component"],
            }
        ),
        flush=True,
    )
    plane.start_staged_run(run_id, {"run_mode": "fully_auto"})
    progress = wait_for(plane, run_id, {"completed", "failed", "aborted"}, args.timeout)
    artifacts = plane.store.list(run_id)
    final_reports = [
        item.artifact_id for item in artifacts if item.artifact_type is ArtifactType.FINAL_REPORT
    ]
    result = {
        "run_id": run_id,
        "status": progress["status"],
        "artifact_count": len(artifacts),
        "final_reports": final_reports,
        "attempts": len(progress.get("attempts", [])),
    }
    print(json.dumps(result), flush=True)
    if progress["status"] != "completed" or not final_reports:
        raise RuntimeError(json.dumps(result))


if __name__ == "__main__":
    main()
