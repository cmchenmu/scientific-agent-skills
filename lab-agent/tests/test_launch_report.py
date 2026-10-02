from pathlib import Path

import pytest

from app.launch_report import (
    ROLLOUT_STAGES,
    build_report,
    count_eval_cases,
    render_markdown,
    validate_rollout_plan,
)


def test_eval_count_and_rollout_order():
    path = Path("tests/evals/knowledge_cases.jsonl")
    assert count_eval_cases(path) >= 30
    validate_rollout_plan(list(ROLLOUT_STAGES), True)
    with pytest.raises(ValueError):
        validate_rollout_plan(list(reversed(ROLLOUT_STAGES)), True)
    with pytest.raises(ValueError):
        validate_rollout_plan(list(ROLLOUT_STAGES), False)


def test_report_without_subprocesses_is_ready():
    report = build_report(
        Path("."), Path("tests/evals/knowledge_cases.jsonl"), run_commands=False
    )

    assert report["ready"] is True
    assert "Launch Readiness Report" in render_markdown(report)
