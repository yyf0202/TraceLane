# ruff: noqa: E501

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "tests/fixtures/coding_tasks/br12_hidden_acceptance_v2.py"
V3 = ROOT / "tests/fixtures/coding_tasks/br12_hidden_acceptance_v3.py"


def _run(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(V2), str(repository)],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_v3(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(V3), str(repository)],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_training(repository: Path) -> None:
    path = repository / "src/cli/kfold_train.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        """
def train(model, trainer, args, loader):
    for epoch in range(1, args.epochs + 1):
        model.set_epoch(epoch, args.epochs)
        trainer.run_epoch(loader, epoch)
""",
        encoding="utf-8",
    )


def _write_variable_launchers(repository: Path, *, beta: str = "0.5") -> None:
    scripts = repository / "scripts"
    scripts.mkdir()
    (scripts / "train_factorvae_phase1.sh").write_text(
        f"""#!/bin/bash
set -e
MODEL=factor_vae
COMMON="--model $MODEL --feature-version v2 --static-features expanded --loss combined --target ranking --temperature 0.3 --k-folds 5 --purge-left 3 --purge-right 65 --start-date 20150101 --end-date 20260325 --seq-len 60 --batch-size 4096 --epochs 200 --lr 1e-3 --weight-decay 1e-4 --seed 42 --z-dim 8"
python src/cli/kfold_train.py kfold-train $COMMON --kl-beta {beta} --lambda-recon 1.0 --kl-warmup-epochs 10 --prior-warmup-epochs 5
if [ "$1" = "--main-only" ]; then exit 0; fi
python src/cli/kfold_train.py kfold-train $COMMON --kl-beta 0.0 --lambda-recon 0.0 --kl-warmup-epochs 0 --prior-warmup-epochs 0
""",
        encoding="utf-8",
    )
    (scripts / "train_factorvae_phase1.bat").write_text(
        f"""@echo off
set "MODEL=factor_vae"
set "COMMON=--model %MODEL% --feature-version v2 --static-features expanded --loss combined --target ranking --temperature 0.3 --k-folds 5 --purge-left 3 --purge-right 65 --start-date 20150101 --end-date 20260325 --seq-len 60 --batch-size 4096 --epochs 200 --lr 1e-3 --weight-decay 1e-4 --seed 42 --z-dim 8"
python src\\cli\\kfold_train.py kfold-train %COMMON% --kl-beta {beta} --lambda-recon 1.0 --kl-warmup-epochs 10 --prior-warmup-epochs 5
if errorlevel 1 goto :error
if "%1"=="--main-only" goto :done
python src\\cli\\kfold_train.py kfold-train %COMMON% --kl-beta 0 --lambda-recon 0 --kl-warmup-epochs 0 --prior-warmup-epochs 0
:done
exit /b 0
:error
exit /b 1
""",
        encoding="utf-8",
    )


def test_v2_accepts_equivalent_variable_based_launchers(tmp_path: Path) -> None:
    _write_training(tmp_path)
    _write_variable_launchers(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_v2_rejects_plausible_launchers_with_wrong_main_semantics(
    tmp_path: Path,
) -> None:
    _write_training(tmp_path)
    _write_variable_launchers(tmp_path, beta="1.0")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert '"earned": 80' in result.stdout


def test_v2_scores_current_kimi_result_by_effective_behavior() -> None:
    repository = Path(
        "/Users/efunyang/Documents/Codex/2026-07-26/"
        "realtime-voice-chat-3/work/"
        "bericher-day3v2-br-12-k2.7code-r1-direct-build"
    )
    if not repository.exists():
        return
    result = _run(repository)
    assert result.returncode == 1
    assert '"earned": 60' in result.stdout


def test_v3_accepts_array_and_python_module_launcher_shape(
    tmp_path: Path,
) -> None:
    _write_training(tmp_path)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "train_factorvae_phase1.sh").write_text(
        """#!/bin/bash
set -e
COMMON=(--model factor_vae --feature-version v2 --static-features expanded --loss combined --target ranking --temperature 0.3 --k-folds 5 --purge-left 3 --purge-right 65 --start-date 20150101 --end-date 20260325 --seq-len 60 --batch-size 4096 --epochs 200 --lr 1e-3 --weight-decay 1e-4 --seed 42 --z-dim 8)
python3 -m src.cli.kfold_train kfold-train "${COMMON[@]}" --kl-beta 0.5 --lambda-recon 1 --kl-warmup-epochs 10 --prior-warmup-epochs 5
if [ "$1" = "--main-only" ]; then exit 0; fi
python3 -m src.cli.kfold_train kfold-train "${COMMON[@]}" --kl-beta 0 --lambda-recon 0 --kl-warmup-epochs 0 --prior-warmup-epochs 0
""",
        encoding="utf-8",
    )
    (scripts / "train_factorvae_phase1.bat").write_text(
        """@echo off
set "COMMON=--model factor_vae --feature-version v2 --static-features expanded --loss combined --target ranking --temperature 0.3 --k-folds 5 --purge-left 3 --purge-right 65 --start-date 20150101 --end-date 20260325 --seq-len 60 --batch-size 4096 --epochs 200 --lr 1e-3 --weight-decay 1e-4 --seed 42 --z-dim 8"
python -m src.cli.kfold_train kfold-train %COMMON% --kl-beta 0.5 --lambda-recon 1 --kl-warmup-epochs 10 --prior-warmup-epochs 5
if errorlevel 1 goto :error
if "%1"=="--main-only" goto :done
python -m src.cli.kfold_train kfold-train %COMMON% --kl-beta 0 --lambda-recon 0 --kl-warmup-epochs 0 --prior-warmup-epochs 0
:done
exit /b 0
:error
exit /b 1
""",
        encoding="utf-8",
    )
    result = _run_v3(tmp_path)
    assert result.returncode == 0, result.stdout


def test_v3_scores_kimi_r2_array_implementation_by_semantics() -> None:
    repository = Path(
        "/Users/efunyang/Documents/Codex/2026-07-26/"
        "realtime-voice-chat-3/work/"
        "bericher-day3v2-br-12-k2.7code-r2-direct-build-"
        "operator-restart-br12-plan-gate-v5"
    )
    if not repository.exists():
        return
    result = _run_v3(repository)
    assert result.returncode == 1
    assert '"earned": 60' in result.stdout
