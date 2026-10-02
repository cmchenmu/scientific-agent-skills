"""Offline launch-readiness report for Chapter 13."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.services.admin_adapter import AdapterError, LocalAdminAdapter
from app.services.release_gate import UnsafeToolInput, validate_tool_arguments

ROLLOUT_STAGES = (
    "knowledge_read_only",
    "analysis_drafts",
    "administrative_drafts",
    "approved_real_writes",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def count_eval_cases(path: Path) -> int:
    return sum(
        1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )


def validate_rollout_plan(stages: list[str], rollback_enabled: bool) -> None:
    if tuple(stages) != ROLLOUT_STAGES:
        raise ValueError("rollout stages must follow the least-privilege order")
    if not rollback_enabled:
        raise ValueError("rollback switch must be enabled")


def red_team_checks() -> list[CheckResult]:
    checks: list[CheckResult] = []
    attacks = {
        "path_traversal": {"path": "../../secrets"},
        "sql_fragment": {"query": "DROP TABLE documents"},
        "shell_fragment": {"command": "safe; cat /etc/passwd"},
        "oversized_input": {"question": "x" * 20_000},
    }
    for name, payload in attacks.items():
        try:
            validate_tool_arguments(payload)
        except UnsafeToolInput:
            checks.append(CheckResult(name, "pass", "rejected"))
        else:
            checks.append(CheckResult(name, "fail", "accepted unsafe input"))

    adapter = LocalAdminAdapter(Path("/tmp/lab-agent-launch-red-team"))
    requester = {
        "id": "student",
        "active": True,
        "roles": ["research-assistant"],
        "project_roles": {"proj": ["research-assistant"]},
        "allowed_tools": {"proj": {"admin.validate_draft"}},
    }
    try:
        adapter.validate_draft(
            requester,
            {
                "project_id": "proj",
                "amount_cents": 1_000_000,
                "currency": "CNY",
                "description": "x",
                "receipts": ["r"],
            },
        )
    except AdapterError:
        checks.append(CheckResult("amount_threshold", "pass", "rejected"))
    else:
        checks.append(
            CheckResult("amount_threshold", "fail", "accepted over-limit amount")
        )
    return checks


def _command_check(name: str, command: list[str], cwd: Path) -> CheckResult:
    completed = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, check=False
    )
    detail = (completed.stdout + completed.stderr).strip().splitlines()
    return CheckResult(
        name,
        "pass" if completed.returncode == 0 else "fail",
        detail[-1] if detail else "no output",
    )


def build_report(
    project_root: Path, eval_path: Path, *, run_commands: bool = True
) -> dict[str, object]:
    checks: list[CheckResult] = []
    if run_commands:
        checks.append(
            _command_check(
                "tests", [sys.executable, "-m", "pytest", "-q"], project_root
            )
        )
        checks.append(
            _command_check(
                "ruff",
                [sys.executable, "-m", "ruff", "check", "app", "tests"],
                project_root,
            )
        )
    count = count_eval_cases(eval_path)
    checks.append(
        CheckResult("eval_cases", "pass" if count >= 30 else "fail", f"{count} cases")
    )
    checks.extend(red_team_checks())
    try:
        validate_rollout_plan(list(ROLLOUT_STAGES), True)
        checks.append(
            CheckResult("rollout_order", "pass", "least-privilege order with rollback")
        )
    except ValueError as error:
        checks.append(CheckResult("rollout_order", "fail", str(error)))
    return {
        "checks": [check.__dict__ for check in checks],
        "ready": all(check.status == "pass" for check in checks),
    }


def render_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Launch Readiness Report",
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for check in report["checks"]:  # type: ignore[union-attr]
        lines.append(f"| {check['name']} | {check['status']} | {check['detail']} |")  # type: ignore[index]
    lines.extend(["", f"**Ready:** `{report['ready']}`"])
    return "\n".join(lines) + "\n"


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    eval_path = project_root / "tests" / "evals" / "knowledge_cases.jsonl"
    report = build_report(project_root, eval_path)
    output = Path("launch-readiness.json")
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path("launch-readiness.md").write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report), end="")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
