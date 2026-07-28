"""Precisely isolated BR-11 behavioral adjudicator.

V5 still let the discovery harness overwrite every path-like global containing
``SIM``, including ``REAL_SIM_PREFIX``.  Its sync slice also reused discovery
behavior, so a discovery failure could erase otherwise valid sync-isolation
credit.  V6 narrows path injection and exercises the sync phase independently.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import br11_hidden_acceptance_v4 as v4
import br11_hidden_acceptance_v5 as v5


def _set_simulations_path(
    module: ModuleType, _fragment: str, value: Path
) -> None:
    candidates = ("SIMULATIONS_DIR", "SIM_BASE_DIR", "SIMULATION_DIR")
    matched = False
    for name in candidates:
        current = getattr(module, name, None)
        if isinstance(current, (str, Path)):
            setattr(module, name, type(current)(value))
            matched = True
    assert matched, "no simulations directory global"


def _sync_isolation(repository: Path) -> None:
    scheduler_path = repository / "scripts/scheduled_daily_run.py"
    sync_path = repository / "scripts/real_trading/sync_real_fills.py"
    v4.v3._load(sync_path, "tracelane_br11_v6_sync_module")
    scheduler = v4.v3._load(
        scheduler_path, "tracelane_br11_v6_sync_scheduler"
    )
    source = scheduler_path.read_text(encoding="utf-8")
    functions = v4.v3._function_sources(source)
    sync_role = v4.v3._role(
        scheduler,
        functions,
        ("sync_real_fills",),
        ("fill", "filled"),
        ("except",),
    )
    role_source = functions[sync_role.__name__]
    role_tree = ast.parse(role_source)
    called_names = {
        node.func.id
        for node in ast.walk(role_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    discovery_names = [
        name
        for name in called_names
        if name in functions
        and "sim_REAL_" in v4.v3._closure(name, functions)
        and "config.json" in v4.v3._closure(name, functions)
    ]
    has_loop = any(isinstance(node, ast.For) for node in ast.walk(role_tree))
    if has_loop:
        assert discovery_names
        for name in discovery_names:
            setattr(
                scheduler,
                name,
                lambda: ["sim_REAL_bad", "sim_REAL_good"],
            )
    for name in called_names:
        if "fill" in name and name != sync_role.__name__:
            value = getattr(scheduler, name, None)
            if callable(value):
                setattr(
                    scheduler,
                    name,
                    lambda sim, date: f"/tmp/{sim}-{date}-filled.csv",
                )

    calls: list[str] = []
    original_run = scheduler.subprocess.run

    def run(command: list[str], **_: object) -> SimpleNamespace:
        sim = command[command.index("--real-sim") + 1]
        calls.append(sim)
        if sim.endswith("bad"):
            raise subprocess.TimeoutExpired(command, 1)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    scheduler.subprocess.run = run
    try:
        parameters = len(
            __import__("inspect").signature(sync_role).parameters
        )
        assert parameters in (1, 2)
        if parameters == 1:
            sync_role("20260728")
        elif has_loop:
            sync_role(["sim_REAL_bad", "sim_REAL_good"], "20260728")
        else:
            bad = sync_role("sim_REAL_bad", "20260728")
            good = sync_role("sim_REAL_good", "20260728")
            assert bad[0] is False
            assert good[0] is True
    finally:
        scheduler.subprocess.run = original_run
    assert calls == ["sim_REAL_bad", "sim_REAL_good"]


def _score(task: str, checks):
    repository = v4._REPOSITORY

    def sync_check() -> None:
        _sync_isolation(repository)

    def pipeline_check() -> None:
        v4._pipeline_check(repository)

    replaced = []
    for name, points, check in checks:
        if name == "per_sim_fill_sync_failure_isolation":
            check = sync_check
        elif name == "pipeline_state_ordering_and_skip_guards":
            check = pipeline_check
        replaced.append((name, points, check))
    return v5._contained_score(task, replaced)


def main(repository: Path) -> int:
    original_setter = v4.v3._set_path_globals
    original_score = v4._decoupled_score
    v4.v3._set_path_globals = _set_simulations_path
    v4._decoupled_score = _score
    try:
        return v4.main(repository)
    finally:
        v4.v3._set_path_globals = original_setter
        v4._decoupled_score = original_score


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
