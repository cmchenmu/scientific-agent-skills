"""Run the deterministic Chapter 9 release gates without external services."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.release_gate import (
    BudgetController,
    CheckpointStore,
    UnsafeToolInput,
    validate_tool_arguments,
)


def run_release_checks(eval_path: Path) -> dict[str, str]:
    checks: dict[str, str] = {}
    try:
        validate_tool_arguments({"path": "../../etc/passwd"})
    except UnsafeToolInput:
        checks["path_traversal"] = "pass"
    else:
        checks["path_traversal"] = "fail"

    budget = BudgetController(1, 10, 1, 4_000_000_000)
    budget.consume(tokens=10, cost_cents=1)
    checks["budget_stop"] = (
        "pass"
        if not budget.consume() and budget.stopped_reason == "max_steps"
        else "fail"
    )

    store = CheckpointStore()
    store.save("demo", {"state": "retrieve", "attempt": 1})
    checks["checkpoint_resume"] = (
        "pass"
        if store.resume("demo") == {"state": "retrieve", "attempt": 1}
        else "fail"
    )

    cases = [
        json.loads(line)
        for line in eval_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    checks["eval_cases"] = "pass" if len(cases) >= 30 else "fail"
    return checks


if __name__ == "__main__":
    result = run_release_checks(Path("tests/evals/knowledge_cases.jsonl"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if all(value == "pass" for value in result.values()) else 1)
