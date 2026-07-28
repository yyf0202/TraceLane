"""Markdown-tolerant Day 3 plan adjudicator.

V3 correctly accepted multilingual BR-11 semantics, but its ordering patterns
were applied to raw Markdown.  Formatting such as ``before **`daily_run`**``
could therefore hide an otherwise explicit ordering statement.  V4 removes
Markdown emphasis/code punctuation before applying the unchanged semantic
checks.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import day3_plan_acceptance_v3 as v3
from day3_plan_acceptance import _br12
from day3_plan_acceptance_v2 import _br10


def _normalize_markdown(content: str) -> str:
    normalized = re.sub(r"[`*]+", " ", content.lower())
    return re.sub(r"[ \t]+", " ", normalized)


def _br11(content: str):
    return v3._br11(_normalize_markdown(content))


def main(plan_path: Path, task_id: str) -> int:
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    content = value.get("content")
    if not isinstance(content, str):
        raise ValueError("plan artifact has no string content")
    builders = {"BR-10": _br10, "BR-11": _br11, "BR-12": _br12}
    outcomes, earned = [], 0
    for name, points, check in builders[task_id](content):
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            outcomes.append(
                {
                    "name": name,
                    "points": points,
                    "earned": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            earned += points
            outcomes.append(
                {"name": name, "points": points, "earned": points}
            )
    print(
        "TRACELANE_PLAN_SCORE="
        + json.dumps(
            {"earned": earned, "possible": 100, "criteria": outcomes},
            sort_keys=True,
        )
    )
    return 0 if earned == 100 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("task_id", choices=("BR-10", "BR-11", "BR-12"))
    args = parser.parse_args()
    raise SystemExit(main(args.plan.resolve(), args.task_id))
