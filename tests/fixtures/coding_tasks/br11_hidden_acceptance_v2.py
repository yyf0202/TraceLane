"""Behavioral BR-11 adjudicator.

Frozen v1 remains the primary preregistered grader.  This version adjudicates
equivalent helper decompositions by executing the scheduler's observable
behavior in an isolated temporary project.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _score(
    task: str, checks: list[tuple[str, int, Callable[[], None]]]
) -> int:
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
        + json.dumps(
            {"earned": earned, "possible": 100, "criteria": outcomes},
            sort_keys=True,
        )
    )
    if earned == 100:
        print(f"{task} behavioral adjudication passed")
        return 0
    return 1


def _candidate(repository: Path) -> tuple[Path, Path]:
    scheduler = repository / "scripts/scheduled_daily_run.py"
    generator = repository / "scripts/real_trading/generate_order_list.py"
    assert scheduler.is_file()
    assert generator.is_file()
    return scheduler, generator


def main(repository: Path) -> int:
    scheduler_path, generator = _candidate(repository)

    def qualified_real_sim_discovery() -> None:
        scheduler = _load(scheduler_path, "tracelane_br11_discovery")
        with tempfile.TemporaryDirectory(prefix="tracelane-br11-discovery-") as raw:
            root = Path(raw)
            valid = root / "sim_REAL_valid"
            valid.mkdir()
            (valid / "config.json").write_text("{}", encoding="utf-8")
            (valid / "state_portfolio.json").write_text("{}", encoding="utf-8")
            missing_state = root / "sim_REAL_missing"
            missing_state.mkdir()
            (missing_state / "config.json").write_text("{}", encoding="utf-8")
            ordinary = root / "sim_ordinary"
            ordinary.mkdir()
            (ordinary / "config.json").write_text("{}", encoding="utf-8")
            (ordinary / "state_portfolio.json").write_text("{}", encoding="utf-8")
            scheduler.SIMULATIONS_DIR = str(root)
            discovered = scheduler._discover_real_sims()
            names = [item.name if isinstance(item, Path) else item for item in discovered]
            assert names == ["sim_REAL_valid"]

    def next_trading_day_with_fallback() -> None:
        scheduler = _load(scheduler_path, "tracelane_br11_calendar")

        class Dates:
            def __init__(self, values: list[str]):
                self.values = values

            def __gt__(self, value: str) -> list[bool]:
                return [item > value for item in self.values]

            def min(self) -> str:
                return min(self.values)

        class Frame:
            def __init__(self, values: list[str]):
                self.values = values

            def __getitem__(self, key: str | list[bool]) -> Dates | Frame:
                if key == "cal_date":
                    return Dates(self.values)
                assert isinstance(key, list)
                return Frame(
                    [
                        value
                        for value, keep in zip(self.values, key, strict=True)
                        if keep
                    ]
                )

            def __len__(self) -> int:
                return len(self.values)

        original_pandas = sys.modules.get("pandas")
        original_exists = scheduler.os.path.exists
        sys.modules["pandas"] = SimpleNamespace(
            read_parquet=lambda _: Frame(["20260731", "20260729", "20260730"])
        )
        scheduler.os.path.exists = lambda path: (
            True if path == scheduler.TRADE_CAL_PATH else original_exists(path)
        )
        try:
            assert scheduler._resolve_next_trading_day("20260728") == "20260729"
            scheduler.os.path.exists = lambda path: (
                False if path == scheduler.TRADE_CAL_PATH else original_exists(path)
            )
            assert scheduler._resolve_next_trading_day("20260731") == "20260803"
        finally:
            scheduler.os.path.exists = original_exists
            if original_pandas is None:
                sys.modules.pop("pandas", None)
            else:
                sys.modules["pandas"] = original_pandas

    def per_sim_fill_sync_failure_isolation() -> None:
        scheduler = _load(scheduler_path, "tracelane_br11_sync")
        scheduler._find_filled_csv = lambda sim, date: f"/tmp/{sim}-{date}.csv"
        calls: list[str] = []
        original_run = scheduler.subprocess.run

        def run(command: list[str], **_: object) -> SimpleNamespace:
            sim = command[command.index("--real-sim") + 1]
            calls.append(sim)
            if sim == "sim_REAL_bad":
                raise subprocess.TimeoutExpired(command, 1)
            return SimpleNamespace(returncode=0, stderr="")

        scheduler.subprocess.run = run
        try:
            bad = scheduler._sync_fills_for_real_sim("sim_REAL_bad", "20260728")
            good = scheduler._sync_fills_for_real_sim("sim_REAL_good", "20260728")
        finally:
            scheduler.subprocess.run = original_run
        assert bad[0] is False
        assert good[0] is True
        assert calls == ["sim_REAL_bad", "sim_REAL_good"]

    def order_generation_from_current_nav() -> None:
        with tempfile.TemporaryDirectory(prefix="tracelane-br11-nav-") as raw:
            root = Path(raw)
            script = root / "scripts/real_trading/generate_order_list.py"
            script.parent.mkdir(parents=True)
            shutil.copy2(generator, script)
            sim = (
                root
                / "paper_trading_data/simulations/sim_REAL_behavioral"
            )
            sim.mkdir(parents=True)
            (sim / "config.json").write_text("{}", encoding="utf-8")
            (sim / "state_portfolio.json").write_text(
                json.dumps(
                    {
                        "cash": 100,
                        "initial_cash": 1000,
                        "positions": {
                            "000001.SZ": {
                                "total_amount": 100,
                                "last_trade_price": 9,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (sim / "state_orders.json").write_text(
                json.dumps(
                    {
                        "orders": [
                            {
                                "symbol": "000001.SZ",
                                "side": "BUY",
                                "target_amount": 100,
                                "target_price": 10,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--real-sim",
                    "sim_REAL_behavioral",
                    "--date",
                    "20260729",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            output = (
                root
                / "paper_trading_data/real_orders/"
                "real_order_20260729_sim_REAL_behavioral.csv"
            )
            assert output.is_file()
            assert "000001.SZ" in output.read_text(encoding="utf-8-sig")

    def pipeline_state_ordering_and_skip_guards() -> None:
        scheduler = _load(scheduler_path, "tracelane_br11_pipeline")
        events: list[str] = []
        subjects: list[str] = []
        scheduler.setup_logging = lambda: None
        scheduler.load_email_config = lambda: {"subject_prefix": "report"}
        scheduler.validate_email_config = lambda _: True
        scheduler._discover_real_sims = lambda: [
            "sim_REAL_bad",
            "sim_REAL_good",
        ]

        def sync(sim: str, _: str) -> tuple[bool, str]:
            events.append(f"sync:{sim}")
            if sim.endswith("bad"):
                raise RuntimeError("isolated sync failure")
            return True, "ok"

        scheduler._sync_fills_for_real_sim = sync

        def daily(*, skip_update: bool) -> bool:
            assert skip_update is False
            events.append("daily")
            return True

        scheduler.run_daily_pipeline = daily
        scheduler._resolve_next_trading_day = lambda _: "20260729"

        def generate(sim: str, _: str) -> tuple[bool, str, int]:
            events.append(f"order:{sim}")
            if sim.endswith("bad"):
                raise RuntimeError("isolated order failure")
            return True, "ok", 3

        scheduler._generate_orders_for_real_sim = generate
        scheduler.find_today_report = lambda: (
            "daily_report_20260728.md",
            "report",
        )
        scheduler.send_email = lambda _, subject, __: subjects.append(subject) or True
        scheduler.sync_data_push = lambda: True
        original_argv = sys.argv
        sys.argv = ["scheduled_daily_run.py", "--force", "--skip-sync"]
        try:
            assert scheduler.main() == 0
        finally:
            sys.argv = original_argv
        assert events == [
            "sync:sim_REAL_bad",
            "sync:sim_REAL_good",
            "daily",
            "order:sim_REAL_bad",
            "order:sim_REAL_good",
        ]
        assert subjects and "3" in subjects[0]

        events.clear()
        scheduler.run_daily_pipeline = (
            lambda *, skip_update: events.append("daily") or False
        )
        sys.argv = [
            "scheduled_daily_run.py",
            "--force",
            "--skip-email",
            "--skip-sync",
            "--skip-real-sync",
        ]
        try:
            assert scheduler.main() == 1
        finally:
            sys.argv = original_argv
        assert events == ["daily"]

    return _score(
        "BR-11",
        [
            ("qualified_real_sim_discovery", 10, qualified_real_sim_discovery),
            ("next_trading_day_with_fallback", 10, next_trading_day_with_fallback),
            (
                "per_sim_fill_sync_failure_isolation",
                20,
                per_sim_fill_sync_failure_isolation,
            ),
            ("order_generation_from_current_nav", 20, order_generation_from_current_nav),
            (
                "pipeline_state_ordering_and_skip_guards",
                40,
                pipeline_state_ordering_and_skip_guards,
            ),
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
