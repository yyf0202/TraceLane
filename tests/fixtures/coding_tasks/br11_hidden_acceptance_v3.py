"""Name-independent behavioral adjudicator for BR-11.

The frozen v1 and v2 results remain unchanged.  V3 discovers implementation
roles from their behavior and call graph instead of requiring prescribed helper
names.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from br11_hidden_acceptance_v2 import _score


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _function_sources(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    return {
        node.name: ast.get_source_segment(source, node) or ""
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _closure(name: str, functions: dict[str, str]) -> str:
    pending, seen, parts = [name], set(), []
    while pending:
        current = pending.pop()
        if current in seen or current not in functions:
            continue
        seen.add(current)
        body = functions[current]
        parts.append(body)
        node = ast.parse(body)
        pending.extend(
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        )
    return "\n".join(parts)


def _role(
    module: ModuleType,
    functions: dict[str, str],
    *term_groups: tuple[str, ...],
) -> Callable[..., object]:
    matches = []
    for name in functions:
        value = getattr(module, name, None)
        if not callable(value):
            continue
        body = name + "\n" + _closure(name, functions)
        if all(any(term in body for term in group) for group in term_groups):
            matches.append((len(body), value))
    assert matches, term_groups
    return min(matches, key=lambda item: item[0])[1]


def _set_path_globals(module: ModuleType, fragment: str, value: Path) -> None:
    for name, current in vars(module).items():
        if fragment in name and isinstance(current, (str, Path)):
            setattr(module, name, type(current)(value))


def _unpack_arity(main_source: str, function_name: str) -> int:
    for node in ast.walk(ast.parse(main_source)):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == function_name
        ):
            return 0
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == function_name
        ):
            continue
        target = node.targets[0]
        if isinstance(target, (ast.Tuple, ast.List)):
            return len(target.elts)
    raise AssertionError(f"no unpacking call for role {function_name}")


def _role_result(arity: int, count: int) -> tuple[object, ...]:
    if arity == 0:
        return (True,)
    assert arity in (2, 3)
    return (True, count) if arity == 2 else (True, "ok", count)


def _patch_pipeline_dependencies(
    module: ModuleType,
    *,
    events: list[str],
    subjects: list[str],
) -> None:
    module.setup_logging = lambda: None
    module.load_email_config = lambda: {"subject_prefix": "report"}
    module.validate_email_config = lambda _: True
    module.is_trading_day = lambda _: True
    module.run_daily_pipeline = (
        lambda *, skip_update: events.append("daily") or True
    )
    module.find_today_report = lambda: ("daily_report_20260728.md", "report")
    module.send_email = (
        lambda _, subject, __: subjects.append(subject) or True
    )
    module.sync_data_push = lambda: True


def main(repository: Path) -> int:
    scheduler_path = repository / "scripts/scheduled_daily_run.py"
    sync_path = repository / "scripts/real_trading/sync_real_fills.py"
    generator_path = repository / "scripts/real_trading/generate_order_list.py"
    scheduler_source = scheduler_path.read_text(encoding="utf-8")
    scheduler_functions = _function_sources(scheduler_source)

    def qualified_real_sim_discovery() -> None:
        scheduler = _load(scheduler_path, "tracelane_br11_v3_discovery")
        discovery = _role(
            scheduler,
            scheduler_functions,
            ("sim_REAL_",),
            ("config.json",),
            ("state_portfolio.json",),
            ("isdir", "is_dir"),
        )
        assert len(inspect.signature(discovery).parameters) == 0
        with tempfile.TemporaryDirectory(prefix="tracelane-br11-v3-discovery-") as raw:
            root = Path(raw)
            valid = root / "sim_REAL_valid"
            valid.mkdir()
            (valid / "config.json").write_text("{}", encoding="utf-8")
            (valid / "state_portfolio.json").write_text("{}", encoding="utf-8")
            incomplete = root / "sim_REAL_incomplete"
            incomplete.mkdir()
            (incomplete / "config.json").write_text("{}", encoding="utf-8")
            ordinary = root / "sim_ordinary"
            ordinary.mkdir()
            (ordinary / "config.json").write_text("{}", encoding="utf-8")
            (ordinary / "state_portfolio.json").write_text("{}", encoding="utf-8")
            _set_path_globals(scheduler, "SIM", root)
            result = discovery()
            values = result.keys() if isinstance(result, dict) else result
            names = [
                item.name if isinstance(item, Path) else Path(str(item)).name
                for item in values
            ]
            assert names == ["sim_REAL_valid"]

    def next_trading_day_with_fallback() -> None:
        scheduler = _load(scheduler_path, "tracelane_br11_v3_calendar")
        resolver = _role(
            scheduler,
            scheduler_functions,
            ("TRADE_CAL_PATH",),
            ("timedelta",),
            ("cal_date",),
        )
        original_exists = scheduler.os.path.exists
        scheduler.os.path.exists = lambda path: (
            False
            if path == getattr(scheduler, "TRADE_CAL_PATH", object())
            else original_exists(path)
        )
        try:
            assert resolver("20260731") == "20260803"
        finally:
            scheduler.os.path.exists = original_exists

    def per_sim_fill_sync_failure_isolation() -> None:
        # Every file used by the scheduled sync path must import in the frozen
        # repository runtime.  This catches annotations that py_compile accepts
        # but Python 3.9 evaluates unsuccessfully at import time.
        _load(sync_path, "tracelane_br11_v3_sync_module")
        scheduler = _load(scheduler_path, "tracelane_br11_v3_sync_scheduler")
        sync_role = _role(
            scheduler,
            scheduler_functions,
            ("sync_real_fills",),
            ("fill", "filled"),
            ("except",),
        )
        assert len(inspect.signature(sync_role).parameters) >= 2

        events: list[str] = []
        subjects: list[str] = []
        _patch_pipeline_dependencies(
            scheduler, events=events, subjects=subjects
        )
        discovery = _role(
            scheduler,
            scheduler_functions,
            ("sim_REAL_",),
            ("config.json",),
            ("state_portfolio.json",),
        )
        setattr(
            scheduler,
            discovery.__name__,
            lambda: {"sim_REAL_bad": ".", "sim_REAL_good": "."},
        )

        sync_arity = _unpack_arity(
            scheduler_functions["main"], sync_role.__name__
        )

        def sync(sim: str, _: str) -> tuple[object, ...]:
            events.append(f"sync:{sim}")
            result = _role_result(sync_arity, 0)
            if sim.endswith("bad"):
                return (False, *result[1:])
            return result

        setattr(scheduler, sync_role.__name__, sync)
        order_role = _role(
            scheduler,
            scheduler_functions,
            ("generate_order", "order_list"),
        )
        order_arity = _unpack_arity(
            scheduler_functions["main"], order_role.__name__
        )
        setattr(
            scheduler,
            order_role.__name__,
            lambda sim, date: (
                events.append(f"order:{sim}")
                or _role_result(order_arity, 1)
            ),
        )
        original_argv = sys.argv
        sys.argv = [
            "scheduled_daily_run.py",
            "--force",
            "--skip-email",
            "--skip-sync",
        ]
        try:
            assert scheduler.main() == 0
        finally:
            sys.argv = original_argv
        assert events[:3] == [
            "sync:sim_REAL_bad",
            "sync:sim_REAL_good",
            "daily",
        ]
        assert events[3:] == [
            "order:sim_REAL_bad",
            "order:sim_REAL_good",
        ]

    def order_generation_from_current_nav() -> None:
        source = generator_path.read_text(encoding="utf-8")
        combined = scheduler_source + "\n" + source
        for term in ("cash", "positions", "total_amount", "last_trade_price"):
            assert term in combined
        assert "+" in combined
        # Execute the generator's real-sim interface when supplied.  Equivalent
        # scheduler-side NAV adapters remain accepted by the source/data-flow
        # check above.
        if "--real-sim" not in source:
            return
        with tempfile.TemporaryDirectory(prefix="tracelane-br11-v3-nav-") as raw:
            root = Path(raw)
            destination = root / "scripts/real_trading/generate_order_list.py"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(generator_path.read_bytes())
            sim = root / "paper_trading_data/simulations/sim_REAL_nav"
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
                    str(destination),
                    "--real-sim",
                    "sim_REAL_nav",
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
            assert (
                root
                / "paper_trading_data/real_orders/"
                "real_order_20260729_sim_REAL_nav.csv"
            ).is_file()

    def pipeline_state_ordering_and_skip_guards() -> None:
        # Independent controls are a user-visible contract, not an internal
        # helper-name convention.
        option_strings = set(
            item
            for item in scheduler_source.split('"')
            if item.startswith("--skip-")
        )
        assert any(
            ("real" in item and ("sync" in item or "fill" in item))
            for item in option_strings
        )
        assert any(
            ("real" in item and ("order" in item or "generate" in item))
            for item in option_strings
        )

        scheduler = _load(scheduler_path, "tracelane_br11_v3_pipeline")
        events: list[str] = []
        subjects: list[str] = []
        _patch_pipeline_dependencies(
            scheduler, events=events, subjects=subjects
        )
        discovery = _role(
            scheduler,
            scheduler_functions,
            ("sim_REAL_",),
            ("config.json",),
            ("state_portfolio.json",),
        )
        setattr(scheduler, discovery.__name__, lambda: ["sim_REAL_one"])
        sync_role = _role(
            scheduler,
            scheduler_functions,
            ("sync_real_fills",),
            ("fill", "filled"),
        )
        order_role = _role(
            scheduler,
            scheduler_functions,
            ("generate_order", "order_list"),
        )
        sync_arity = _unpack_arity(
            scheduler_functions["main"], sync_role.__name__
        )
        order_arity = _unpack_arity(
            scheduler_functions["main"], order_role.__name__
        )
        setattr(
            scheduler,
            sync_role.__name__,
            lambda sim, date: (
                events.append("sync") or _role_result(sync_arity, 0)
            ),
        )
        setattr(
            scheduler,
            order_role.__name__,
            lambda sim, date: (
                events.append("order") or _role_result(order_arity, 3)
            ),
        )
        original_argv = sys.argv
        sys.argv = ["scheduled_daily_run.py", "--force", "--skip-sync"]
        try:
            assert scheduler.main() == 0
        finally:
            sys.argv = original_argv
        assert events == ["sync", "daily", "order"]
        assert subjects and "3" in subjects[0]

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
