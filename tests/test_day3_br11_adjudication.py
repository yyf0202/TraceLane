from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "tests/fixtures/coding_tasks/br11_hidden_acceptance_v2.py"
V3 = ROOT / "tests/fixtures/coding_tasks/br11_hidden_acceptance_v3.py"
V4 = ROOT / "tests/fixtures/coding_tasks/br11_hidden_acceptance_v4.py"
AMENDMENT = (
    ROOT
    / "fixtures/coding/bericher-v0.9/day3-br11-disconnect-recovery.json"
)
GRADER_PYTHON = Path(
    "/Users/efunyang/Desktop/BeRicher_v0.45/.venv/bin/python"
)
sys.path.insert(0, str(ROOT / "scripts"))

import resume_day3_after_br11_v3 as v3_recovery  # noqa: E402
import resume_day3_after_br11_v4 as v4_recovery  # noqa: E402
import resume_day3_after_disconnect as recovery  # noqa: E402
import run_day3_coding_eval as day3  # noqa: E402


def _write_candidate(repository: Path, *, incompatible: bool = False) -> None:
    scheduler = repository / "scripts/scheduled_daily_run.py"
    generator = repository / "scripts/real_trading/generate_order_list.py"
    scheduler.parent.mkdir(parents=True)
    generator.parent.mkdir(parents=True)
    annotation = " -> str | None" if incompatible else ""
    scheduler.write_text(
        f"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADE_CAL_PATH = os.path.join(PROJECT_ROOT, "calendar.parquet")
SIMULATIONS_DIR = os.path.join(PROJECT_ROOT, "simulations")
REAL_ORDERS_DIR = os.path.join(PROJECT_ROOT, "real_orders")

def _discover_real_sims():
    if not os.path.isdir(SIMULATIONS_DIR):
        return []
    return [
        name for name in sorted(os.listdir(SIMULATIONS_DIR))
        if name.startswith("sim_REAL_")
        and os.path.isdir(os.path.join(SIMULATIONS_DIR, name))
        and os.path.exists(os.path.join(SIMULATIONS_DIR, name, "config.json"))
        and os.path.exists(os.path.join(SIMULATIONS_DIR, name, "state_portfolio.json"))
    ]

def _resolve_next_trading_day(value):
    if os.path.exists(TRADE_CAL_PATH):
        try:
            import pandas as pd
            frame = pd.read_parquet(TRADE_CAL_PATH)
            future = frame[frame["cal_date"] > value]
            if len(future):
                return future["cal_date"].min()
        except Exception:
            pass
    date = datetime.strptime(value, "%Y%m%d") + timedelta(days=1)
    while date.weekday() >= 5:
        date += timedelta(days=1)
    return date.strftime("%Y%m%d")

def _find_filled_csv(sim, date){annotation}:
    return os.path.join(REAL_ORDERS_DIR, f"{{sim}}-{{date}}.csv")

def _sync_fills_for_real_sim(sim, date):
    try:
        result = subprocess.run(
            [sys.executable, "sync_real_fills.py", "--real-sim", sim,
             "--fills-csv", _find_filled_csv(sim, date), "--date", date],
            timeout=30, capture_output=True, text=True,
        )
        return result.returncode == 0, result.stderr
    except (subprocess.TimeoutExpired, Exception) as error:
        return False, str(error)

def setup_logging():
    pass
def load_email_config():
    return {{"subject_prefix": "report"}}
def validate_email_config(config):
    return True
def run_daily_pipeline(*, skip_update):
    return True
def _generate_orders_for_real_sim(sim, date):
    return True, "ok", 1
def find_today_report():
    return "daily_report_20260728.md", "report"
def send_email(config, subject, body):
    return True
def sync_data_push():
    return True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-update", action="store_true")
    parser.add_argument("--skip-email", action="store_true")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--skip-real-sync", action="store_true")
    parser.add_argument("--skip-real-orders", action="store_true")
    parser.add_argument("--date")
    args = parser.parse_args()
    setup_logging()
    config = load_email_config()
    email_ok = validate_email_config(config)
    sims = _discover_real_sims()
    if not args.skip_real_sync:
        for sim in sims:
            try:
                _sync_fills_for_real_sim(sim, args.date or "20260728")
            except Exception:
                pass
    ok = run_daily_pipeline(skip_update=args.skip_update)
    results = {{}}
    if ok and not args.skip_real_orders:
        next_day = _resolve_next_trading_day(args.date or "20260728")
        for sim in sims:
            try:
                good, message, count = _generate_orders_for_real_sim(sim, next_day)
                results[sim] = {{"ok": good, "order_count": count}}
            except Exception:
                results[sim] = {{"ok": False, "order_count": 0}}
    if not args.skip_email and email_ok:
        path, body = find_today_report()
        count = sum(item["order_count"] for item in results.values() if item["ok"])
        send_email(config, f"report {{count}}", body)
    if not args.skip_sync:
        sync_data_push()
    return 0 if ok else 1
""",
        encoding="utf-8",
    )
    generator.write_text(
        """
import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--real-sim", required=True)
parser.add_argument("--date", required=True)
args = parser.parse_args()
sim = ROOT / "paper_trading_data/simulations" / args.real_sim
portfolio = json.loads((sim / "state_portfolio.json").read_text())
orders = json.loads((sim / "state_orders.json").read_text())["orders"]
cash = portfolio["cash"]
positions = portfolio["positions"]
nav = cash + sum(
    item["total_amount"] * item["last_trade_price"]
    for item in positions.values()
)
ratio = nav / nav
output = ROOT / "paper_trading_data/real_orders"
output.mkdir(parents=True)
with (output / f"real_order_{args.date}_{args.real_sim}.csv").open("w") as handle:
    writer = csv.writer(handle)
    writer.writerow(["symbol", "target_amount"])
    for order in orders:
        writer.writerow([order["symbol"], int(order["target_amount"] * ratio)])
""",
        encoding="utf-8",
    )
    (generator.parent / "sync_real_fills.py").write_text(
        """
from pathlib import Path

def find_fills_csv(real_sim_id: str, date: str):
    return Path(f"{real_sim_id}-{date}-filled.csv")

def sync_real_fills(real_sim_id: str, fills_csv: Path, date: str):
    return True, "ok", 1
""",
        encoding="utf-8",
    )


def _run(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(GRADER_PYTHON), str(V2), str(repository)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _run_v3(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(GRADER_PYTHON), str(V3), str(repository)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _run_v4(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(GRADER_PYTHON), str(V4), str(repository)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _score(result: subprocess.CompletedProcess[str]) -> int:
    line = next(
        line for line in result.stdout.splitlines() if line.startswith("TRACELANE_SCORE=")
    )
    return json.loads(line.removeprefix("TRACELANE_SCORE="))["earned"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_br11_v2_accepts_behaviorally_correct_helper_decomposition(
    tmp_path: Path,
) -> None:
    _write_candidate(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout
    assert _score(result) == 100


def test_br11_v2_rejects_plausible_python39_incompatible_scheduler(
    tmp_path: Path,
) -> None:
    _write_candidate(tmp_path, incompatible=True)
    result = _run(tmp_path)
    assert result.returncode == 1
    assert _score(result) == 20


def test_br11_v3_accepts_equivalent_behavior_without_prescribed_names(
    tmp_path: Path,
) -> None:
    _write_candidate(tmp_path)
    result = _run_v3(tmp_path)
    assert result.returncode == 0, result.stdout
    assert _score(result) == 100


def test_br11_v3_rejects_shared_real_trading_skip_switch(
    tmp_path: Path,
) -> None:
    _write_candidate(tmp_path)
    scheduler = tmp_path / "scripts/scheduled_daily_run.py"
    source = scheduler.read_text(encoding="utf-8")
    source = source.replace("--skip-real-sync", "--skip-real-trading")
    source = source.replace(
        '    parser.add_argument("--skip-real-orders", action="store_true")\n',
        "",
    )
    source = source.replace("args.skip_real_sync", "args.skip_real_trading")
    source = source.replace("args.skip_real_orders", "args.skip_real_trading")
    scheduler.write_text(source, encoding="utf-8")
    result = _run_v3(tmp_path)
    assert result.returncode == 1
    assert _score(result) == 60


def test_br11_v4_keeps_pipeline_slice_independent(tmp_path: Path) -> None:
    _write_candidate(tmp_path)
    result = _run_v4(tmp_path)
    assert result.returncode == 0, result.stdout
    assert _score(result) == 100


def test_br11_v4_still_rejects_shared_skip_switch(tmp_path: Path) -> None:
    _write_candidate(tmp_path)
    scheduler = tmp_path / "scripts/scheduled_daily_run.py"
    source = scheduler.read_text(encoding="utf-8")
    source = source.replace("--skip-real-sync", "--skip-real-trading")
    source = source.replace(
        '    parser.add_argument("--skip-real-orders", action="store_true")\n',
        "",
    )
    source = source.replace("args.skip_real_sync", "args.skip_real_trading")
    source = source.replace("args.skip_real_orders", "args.skip_real_trading")
    scheduler.write_text(source, encoding="utf-8")
    result = _run_v4(tmp_path)
    assert result.returncode == 1
    assert _score(result) == 60


def test_disconnect_resume_starts_after_completed_replacement() -> None:
    original = day3.matrix()
    interrupted_index = next(
        index
        for index, row in enumerate(original)
        if row.run_slug == recovery.INTERRUPTED
    )
    remaining = recovery.remaining_matrix()
    assert remaining == original[interrupted_index + 1 :]
    assert all(row.run_slug != recovery.COMPLETED_RESTART for row in remaining)


def test_disconnect_recovery_amendment_hashes() -> None:
    value = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    frozen = value["frozen_inputs"]
    assert _sha256(V2) == frozen["functional_adjudicator_sha256"]
    assert _sha256(
        ROOT / "scripts/resume_day3_after_disconnect.py"
    ) == frozen["resume_runner_sha256"]


def test_br11_v3_resume_replaces_only_interrupted_r2_direct() -> None:
    original = day3.matrix()
    index = next(
        index
        for index, row in enumerate(original)
        if row.run_slug == v3_recovery.INTERRUPTED
    )
    remaining = v3_recovery.remaining_matrix()
    assert remaining[0].run_slug == (
        v3_recovery.INTERRUPTED + "-" + v3_recovery.RESTART_SUFFIX
    )
    assert remaining[1:] == original[index + 1 :]


def test_br11_v3_amendment_hashes() -> None:
    amendment = (
        ROOT
        / "fixtures/coding/bericher-v0.9/day3-br11-adjudication-v3.json"
    )
    value = json.loads(amendment.read_text(encoding="utf-8"))
    frozen = value["frozen_inputs"]
    assert _sha256(V3) == frozen["functional_adjudicator_sha256"]
    assert _sha256(
        ROOT / "scripts/resume_day3_after_br11_v3.py"
    ) == frozen["resume_runner_sha256"]


def test_br11_v4_resume_replaces_only_interrupted_r2_plan() -> None:
    original = day3.matrix()
    index = next(
        index
        for index, row in enumerate(original)
        if row.run_slug == v4_recovery.INTERRUPTED
    )
    remaining = v4_recovery.remaining_matrix()
    assert remaining[0].run_slug == (
        v4_recovery.INTERRUPTED + "-" + v4_recovery.RESTART_SUFFIX
    )
    assert remaining[1:] == original[index + 1 :]


def test_br11_v4_amendment_hashes() -> None:
    amendment = (
        ROOT
        / "fixtures/coding/bericher-v0.9/day3-br11-adjudication-v4.json"
    )
    value = json.loads(amendment.read_text(encoding="utf-8"))
    frozen = value["frozen_inputs"]
    assert _sha256(V4) == frozen["functional_adjudicator_sha256"]
    assert _sha256(
        ROOT / "scripts/resume_day3_after_br11_v4.py"
    ) == frozen["resume_runner_sha256"]
