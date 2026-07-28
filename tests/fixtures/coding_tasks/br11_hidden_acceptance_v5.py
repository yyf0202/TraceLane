"""Crash-contained BR-11 functional-slice adjudicator.

V4 decoupled the pipeline slice, but delegated scoring to a helper that caught
``Exception`` only.  Candidate CLIs commonly use ``argparse``, whose parse
failures raise ``SystemExit``; one such failure therefore aborted the entire
grader instead of producing a zero for the affected slice.  V5 preserves all
V4 semantics and contains ``SystemExit`` at the slice boundary.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import br11_hidden_acceptance_v4 as v4


def _contained_score(
    task: str, checks: list[tuple[str, int, Callable[[], None]]]
) -> int:
    outcomes, earned = [], 0
    for name, points, check in checks:
        try:
            check()
        except (Exception, SystemExit) as exc:  # noqa: BLE001
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
        "TRACELANE_SCORE="
        + json.dumps(
            {"earned": earned, "possible": 100, "criteria": outcomes},
            sort_keys=True,
        )
    )
    if earned == 100:
        print(f"{task} behavioral adjudication passed")
        return 0
    return 1


def main(repository: Path) -> int:
    original = v4._ORIGINAL_SCORE
    v4._ORIGINAL_SCORE = _contained_score
    try:
        return v4.main(repository)
    finally:
        v4._ORIGINAL_SCORE = original


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
