"""Day 2 v4 plan gate rejecting contradictory BR-08 loop pseudocode."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path

from day2_plan_acceptance import _br07
from day2_v2_plan_acceptance import _br06
from day2_v3_plan_acceptance import _br08 as _br08_v3


def _br08(content: str) -> list[tuple[str, int, Callable[[], None]]]:
    checks = _br08_v3(content)
    low = content.lower()
    enhanced: list[tuple[str, int, Callable[[], None]]] = []
    for name, points, check in checks:
        if name != "grouped_earliest_range_catchup":
            enhanced.append((name, points, check))
            continue

        def grouping(check: Callable[[], None] = check) -> None:
            check()
            assert not re.search(
                r"for\s+\w*sim\w*\s+in\s+[\w.()]+\s*:"
                r"[\s\S]{0,240}run_date_range_multi",
                low,
            ), "grouped prose contradicts per-simulation range-call pseudocode"

        enhanced.append((name, points, grouping))
    return enhanced


def main(plan_path: Path, task_id: str) -> int:
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    content = value.get("content")
    if not isinstance(content, str):
        raise ValueError("plan artifact has no string content")
    builders = {"BR-06": _br06, "BR-07": _br07, "BR-08": _br08}
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
            outcomes.append({"name": name, "points": points, "earned": points})
    print(
        "TRACELANE_PLAN_SCORE="
        + json.dumps({"earned": earned, "possible": 100, "criteria": outcomes}, sort_keys=True)
    )
    return 0 if earned == 100 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("task_id", choices=("BR-06", "BR-07", "BR-08"))
    args = parser.parse_args()
    raise SystemExit(main(args.plan.resolve(), args.task_id))
