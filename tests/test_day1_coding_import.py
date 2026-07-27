from __future__ import annotations

import json
from pathlib import Path

from scripts.import_day1_coding_eval import _cli_summary


def test_cli_summary_aggregates_plan_and_build_costs(tmp_path: Path) -> None:
    paths = []
    for index, (name, session_id, amount) in enumerate(
        (
            ("plan-cli.jsonl", "ses_plan", 0.1),
            ("build-cli.jsonl", "ses_build", 0.2),
        )
    ):
        path = tmp_path / name
        rows = [
            {
                "type": "step_finish",
                "timestamp": 1_000 + index * 2_000,
                "sessionID": session_id,
                "part": {
                    "type": "step-finish",
                    "cost": amount,
                    "tokens": {
                        "input": 10,
                        "output": 3,
                        "reasoning": 1,
                        "cache": {"read": 5},
                    },
                },
            },
            {
                "type": "text",
                "timestamp": 2_000 + index * 2_000,
                "sessionID": session_id,
                "part": {"type": "text", "text": f"{name} done"},
            },
        ]
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        paths.append(path)

    summary = _cli_summary(tuple(paths))

    assert summary["session_ids"] == ["ses_plan", "ses_build"]
    assert summary["amount"] == 0.3
    assert summary["input_tokens"] == 20
    assert summary["cached_input_tokens"] == 10
    assert summary["output_tokens"] == 6
    assert summary["reasoning_tokens"] == 2
    assert summary["wall_ms"] == 2_000
    assert summary["final_answer"] == "build-cli.jsonl done"
