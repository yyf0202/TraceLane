"""Semantic plan gate for the frozen BR-10 through BR-12 Day 3 tasks."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path


def _any(content: str, *patterns: str) -> bool:
    return any(re.search(pattern, content, re.IGNORECASE) for pattern in patterns)


def _all(content: str, groups: tuple[tuple[str, ...], ...]) -> None:
    for group in groups:
        assert _any(content, *group), f"missing semantic group: {group}"


def _br10(content: str) -> list[tuple[str, int, Callable[[], None]]]:
    low = content.lower()

    def cross_platform_scope() -> None:
        _all(
            low,
            (
                (r"sync_data\.sh", r"posix", r"shell"),
                (r"sync_data\.bat", r"windows", r"batch"),
                (r"incremental",),
                (r"stock", r"data repo"),
            ),
        )

    def push_existing_ahead_commits() -> None:
        _all(
            low,
            (
                (r"push",),
                (r"no (new )?(change|commit)", r"nothing .*commit", r"ahead commit"),
                (
                    r"commit .*conditional",
                    r"commit (only|if)",
                    r"only .*commit",
                    r"only if .*commit",
                ),
            ),
        )
        assert not _any(
            low,
            r"(skip|avoid|do not|don't) (the )?push .*no (new )?change",
            r"push only .*new (change|commit)",
        )

    def staged_large_file_preflight() -> None:
        _all(
            low,
            (
                (r"staged", r"cached"),
                (r"100\s*(mib|mb)", r"104857600"),
                (r"added.*copied.*modified", r"acm", r"diff-filter"),
                (r"stat .*-c", r"linux"),
                (r"stat .*-f", r"macos", r"darwin"),
                (r"%%~z", r"batch.*size", r"windows.*size"),
            ),
        )

    def abort_and_date_semantics() -> None:
        _all(
            low,
            (
                (r"abort", r"skip .*stock.*push", r"prevent .*stock.*push"),
                (r"independent", r"incremental .*continue", r"stock .*only"),
                (r"!datestr!", r"delayed .*expansion"),
                (r"git diff --check", r"bash -n", r"validation"),
            ),
        )
        assert not _any(low, r"abort (the )?(entire|both) .*flow")

    return [
        ("cross_platform_independent_flows", 20, cross_platform_scope),
        ("push_existing_ahead_commits", 30, push_existing_ahead_commits),
        ("staged_large_file_preflight", 30, staged_large_file_preflight),
        ("abort_scope_date_and_validation", 20, abort_and_date_semantics),
    ]


def _br11(content: str) -> list[tuple[str, int, Callable[[], None]]]:
    low = content.lower()

    def qualified_discovery() -> None:
        _all(
            low,
            (
                (r"sim_real",),
                (r"config\.json",),
                (r"state_portfolio\.json",),
                (r"director", r"folder", r"path"),
            ),
        )

    def sync_before_pipeline() -> None:
        _all(
            low,
            (
                (r"filled.*csv", r"_filled\.csv"),
                (r"sync_real_fills",),
                (r"before .*daily", r"step 0", r"sync.*then.*daily"),
                (r"per.sim", r"each sim", r"isolate"),
                (r"timeout", r"returncode", r"subprocess"),
            ),
        )
        assert not _any(
            low,
            r"(one|single) .*failure (will |should )?(abort|stop).*(all|daily)",
            r"(abort|stop).*(all|daily) on .*failure",
        )

    def post_success_next_day() -> None:
        _all(
            low,
            (
                (r"after .*daily", r"daily.*succe", r"pipeline_ok"),
                (r"next trading day", r"trade calendar", r"cal_date"),
                (r"fallback", r"timedelta", r"next calendar day"),
                (r"generate_order_list", r"order list"),
            ),
        )
        assert not _any(low, r"generat.*order.*before .*daily")

    def nav_guards_and_email() -> None:
        _all(
            low,
            (
                (r"cash",),
                (r"position",),
                (r"nav", r"marked.*value", r"last_trade_price"),
                (r"skip.real.sync",),
                (r"skip.real.orders",),
                (r"subject", r"email"),
                (r"count",),
            ),
        )

    return [
        ("qualified_real_sim_discovery", 20, qualified_discovery),
        ("fill_sync_before_pipeline_with_isolation", 30, sync_before_pipeline),
        ("post_success_next_trading_day_orders", 30, post_success_next_day),
        ("current_nav_skip_guards_and_email_count", 20, nav_guards_and_email),
    ]


def _br12(content: str) -> list[tuple[str, int, Callable[[], None]]]:
    low = content.lower()

    def epoch_state_timing() -> None:
        _all(
            low,
            (
                (r"set_epoch", r"advance .*epoch"),
                (r"inside .*epoch", r"each .*epoch", r"per.epoch"),
                (r"before .*run_epoch", r"before .*train"),
                (r"args\.epochs", r"total .*epochs"),
            ),
        )
        assert not _any(
            low,
            r"set_epoch.{0,80}(outside|before) .*epoch loop",
            r"once .*set_epoch",
        )

    def cross_platform_recipe() -> None:
        _all(
            low,
            (
                (r"train_factorvae_phase1\.sh", r"posix", r"shell"),
                (r"train_factorvae_phase1\.bat", r"windows", r"batch"),
                (r"same .*recipe", r"parity", r"equivalent"),
                (r"k.fold", r"purge"),
                (r"feature", r"static"),
                (r"seed", r"batch"),
            ),
        )

    def active_and_inert_arms() -> None:
        _all(
            low,
            (
                (r"main arm", r"full .*vae"),
                (r"prior.only", r"control arm"),
                (r"kl.beta.*0\.5", r"beta.*0\.5"),
                (r"lambda.recon.*1", r"recon.*1"),
                (r"kl.beta.*0", r"beta.*zero"),
                (r"lambda.recon.*0", r"recon.*zero"),
                (r"warmup",),
            ),
        )

    def short_circuit_control() -> None:
        _all(
            low,
            (
                (r"main.only",),
                (r"skips? (the )?control", r"do not .*control"),
                (r"main .*fail", r"non.zero", r"errorlevel", r"set -e"),
                (r"prevent .*control", r"stop .*before .*control", r"short.circuit"),
                (r"py_compile", r"bash -n", r"validation"),
            ),
        )
        assert not _any(low, r"(always|regardless).*run .*control")

    return [
        ("per_epoch_state_before_training", 35, epoch_state_timing),
        ("cross_platform_recipe_parity", 20, cross_platform_recipe),
        ("active_and_inert_control_arms", 25, active_and_inert_arms),
        ("main_only_failure_short_circuit_and_validation", 20, short_circuit_control),
    ]


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
            outcomes.append({"name": name, "points": points, "earned": points})
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
