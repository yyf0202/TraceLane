"""Functional-slice acceptance for BR-09 V5 simulation routing."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path


def main(repository: Path) -> int:
    runner = (repository / "src/paper_trading/daily_runner.py").read_text(encoding="utf-8")
    rebuild = (repository / "scripts/rebuild_bt2025.py").read_text(encoding="utf-8")

    def range_datasource() -> None:
        section = runner[
            runner.index("def run_date_range_multi") : runner.index(
                "def ", runner.index("def run_date_range_multi") + 10
            )
        ]
        assert "resolve_feature_version" in section
        assert 'training_version in ("alpha", "alpha_plus")' in section
        assert 'training_version == "alpha_plus"' in section

    def daily_datasource() -> None:
        section = runner[runner.index("def run_daily_cycle") :]
        assert 'training_version in ("alpha", "alpha_plus")' in section
        assert 'training_version in ("v3", "alpha_plus")' in section

    def clone_discovery() -> None:
        assert '_CLONE_PREFIXES = ("sim_kfold_v46_", "sim_kfold_v5_")' in rebuild
        assert "any(d.startswith(p) for p in _CLONE_PREFIXES)" in rebuild

    def universal_shortening() -> None:
        assert "def _shorten_sim_id" in rebuild
        assert 'sim_id.startswith("sim_kfold_")' in rebuild
        assert rebuild.count('_shorten_sim_id(sd["sim_id"])') >= 2

    checks = [
        ("range_alpha_datasource_routing", 30, range_datasource),
        ("daily_alpha_datasource_routing", 30, daily_datasource),
        ("v46_and_v5_clone_discovery", 20, clone_discovery),
        ("universal_sim_id_shortening", 20, universal_shortening),
    ]
    return _score("BR-09", checks)


def _score(task: str, checks: list[tuple[str, int, Callable[[], None]]]) -> int:
    outcomes, earned = [], 0
    for name, points, check in checks:
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
        "TRACELANE_SCORE="
        + json.dumps({"earned": earned, "possible": 100, "criteria": outcomes}, sort_keys=True)
    )
    if earned == 100:
        print(f"{task} independent acceptance passed")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
