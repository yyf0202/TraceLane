from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).parent / "fixtures/coding_tasks/day3_plan_acceptance.py"


def _run(tmp_path: Path, task: str, content: str) -> subprocess.CompletedProcess[str]:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"content": content}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(GATE), str(plan), task],
        capture_output=True,
        text=True,
        check=False,
    )


def test_br10_accepts_equivalent_complete_semantics(tmp_path: Path) -> None:
    content = """
    Update scripts/sync_data.sh (POSIX shell) and scripts/sync_data.bat (Windows batch)
    while preserving the independent incremental and stock data repo flows. Commit only
    if the cached diff is dirty, but push even with no new changes so ahead commits move.
    After staging, inspect added/copied/modified files (diff-filter ACM) and reject files
    over 100 MiB / 104857600 bytes. Shell uses Linux stat -c with macOS stat -f fallback;
    batch uses %%~zf. Abort only the stock push, allow incremental to continue, and use
    delayed expansion !DATESTR!. Validate with bash -n and git diff --check.
    """
    result = _run(tmp_path, "BR-10", content)
    assert result.returncode == 0, result.stdout


def test_br11_accepts_equivalent_complete_semantics(tmp_path: Path) -> None:
    content = """
    Discover sorted sim_REAL directories only when config.json and state_portfolio.json
    exist. Step 0 matches _filled.csv and invokes sync_real_fills before daily_run, with
    timeout/returncode handling isolated for each sim so one failure does not abort all.
    Only after daily pipeline_ok, resolve the next trading day from cal_date in the trade
    calendar with a timedelta next-calendar-day fallback, then call generate_order_list.
    Compute NAV from cash plus positions marked by last_trade_price. Add --skip-real-sync
    and --skip-real-orders and include the generated order count in the email subject.
    """
    result = _run(tmp_path, "BR-11", content)
    assert result.returncode == 0, result.stdout


def test_br12_accepts_equivalent_complete_semantics(tmp_path: Path) -> None:
    content = """
    Inside each epoch loop call model.set_epoch(epoch, args.epochs) before run_epoch.
    Add POSIX train_factorvae_phase1.sh and Windows train_factorvae_phase1.bat with the
    same feature/static, k-fold/purge, batch and seed recipe. The full VAE main arm uses
    kl-beta 0.5, lambda-recon 1 and warmup; the prior-only control arm uses kl-beta 0 and
    lambda-recon 0. A --main-only switch skips the control. A non-zero main failure must
    short-circuit and prevent control via set -e / errorlevel. Validate py_compile and
    bash -n.
    """
    result = _run(tmp_path, "BR-12", content)
    assert result.returncode == 0, result.stdout


def test_gates_reject_correct_words_with_wrong_semantics(tmp_path: Path) -> None:
    wrong = {
        "BR-10": """
            Change shell and Windows batch for incremental and stock repos. Use staged ACM,
            100 MiB, Linux stat -c, macOS stat -f, %%~zf, !DATESTR!, abort stock only and
            validate bash -n. Commit conditionally, but skip the push when no new changes.
        """,
        "BR-11": """
            Discover sim_REAL with config.json/state_portfolio.json. Sync _filled.csv per-sim
            using sync_real_fills, timeout and returncode before daily. Resolve next trading
            day with fallback, then generate orders before daily_run using cash, positions
            and NAV. Add both skip-real flags and email subject count.
        """,
        "BR-12": """
            Call set_epoch once outside the epoch loop before training with args.epochs.
            Add matching POSIX shell and Windows batch recipes with features, static, k-fold,
            purge, batch and seed. Include full main beta 0.5/recon 1/warmup and prior-only
            beta 0/recon 0 arms. main-only skips control; failure short-circuits via set -e,
            errorlevel. Validate py_compile and bash -n.
        """,
    }
    for task, content in wrong.items():
        result = _run(tmp_path, task, content)
        assert result.returncode == 1, (task, result.stdout)
