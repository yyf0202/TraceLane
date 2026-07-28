from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).parent / "fixtures/coding_tasks/day3_plan_acceptance.py"
GATE_V3 = (
    Path(__file__).parent
    / "fixtures/coding_tasks/day3_plan_acceptance_v3.py"
)
GATE_V5 = (
    Path(__file__).parent
    / "fixtures/coding_tasks/day3_plan_acceptance_v5.py"
)


def _run(tmp_path: Path, task: str, content: str) -> subprocess.CompletedProcess[str]:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"content": content}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(GATE), str(plan), task],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_v3(
    tmp_path: Path, task: str, content: str
) -> subprocess.CompletedProcess[str]:
    plan = tmp_path / "plan-v3.json"
    plan.write_text(json.dumps({"content": content}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(GATE_V3), str(plan), task],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_v5(
    tmp_path: Path, task: str, content: str
) -> subprocess.CompletedProcess[str]:
    plan = tmp_path / "plan-v5.json"
    plan.write_text(json.dumps({"content": content}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(GATE_V5), str(plan), task],
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


def test_br11_v3_accepts_complete_chinese_semantics(tmp_path: Path) -> None:
    content = """
    扫描 sim_REAL_* 目录，且必须同时存在 config.json 和
    state_portfolio.json。预 daily_run 对每个 sim 查找 filled CSV 并调用
    sync_fills；用 try/except 将失败限制在单个 sim，继续处理其他 sim。
    daily_run 成功后，从交易日历 cal_date 解析下一交易日；不可用时回退到
    工作日递增并跳过周六周日，再调用 generate_order_list 生成下单清单。
    当前 NAV 使用现金 cash 加持仓 position 的 last_trade_price 市值。
    增加 --skip-real-sync 和 --skip-real-orders 两个独立开关，并把订单数量
    写入邮件主题。
    """
    result = _run_v3(tmp_path, "BR-11", content)
    assert result.returncode == 0, result.stdout


def test_br11_v3_rejects_chinese_plan_with_wrong_order(tmp_path: Path) -> None:
    content = """
    扫描 sim_REAL_* 目录并检查 config.json 和 state_portfolio.json。
    在 daily_run 之前，对每个 sim 使用 try/except 隔离失败并调用
    sync_fills 处理 filled CSV。
    从交易日历 cal_date 获取下一交易日，失败时回退到工作日递增。
    使用现金和持仓的 last_trade_price 计算 NAV，提供 --skip-real-sync 与
    --skip-real-orders，将订单数量放入邮件主题；但在 daily_run 之前生成
    generate_order_list 下单清单。
    """
    result = _run_v3(tmp_path, "BR-11", content)
    assert result.returncode == 1


def test_br12_v5_accepts_failed_main_blocks_control_wording(
    tmp_path: Path,
) -> None:
    content = """
    Inside each epoch loop call model.set_epoch(epoch, args.epochs) before run_epoch.
    Add equivalent POSIX shell and Windows batch launchers with the
    same model, feature/static, k-fold/purge, date, batch and seed recipe. The
    full VAE main arm uses kl-beta 0.5, lambda-recon 1 and warmup; the prior-only
    control uses kl-beta 0 and lambda-recon 0. --main-only skips the control.
    A failed main arm blocks the control arm. Validate with py_compile, bash -n,
    and git diff --check.
    """
    result = _run_v5(tmp_path, "BR-12", content)
    assert result.returncode == 0, result.stdout


def test_br12_v5_still_rejects_wrong_recipe_despite_equivalent_block_wording(
    tmp_path: Path,
) -> None:
    content = """
    Inside each epoch loop call model.set_epoch(epoch, args.epochs) before run_epoch.
    Add equivalent POSIX shell and Windows batch launchers with the
    same model, feature/static, k-fold/purge, date, batch and seed recipe. The
    full VAE main arm uses kl-beta 1.0, lambda-recon 1 and warmup; the control
    leaves lambda-recon at 1. --main-only skips the control. A failed main arm
    blocks the control arm. Validate with py_compile, bash -n, and git
    diff --check.
    """
    result = _run_v5(tmp_path, "BR-12", content)
    assert result.returncode == 1
    assert '"earned": 75' in result.stdout
