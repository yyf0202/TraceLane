"""Semantic BR-12 adjudicator.

The frozen v1 grader searched launcher source for literal command fragments.
That rejected equivalent launchers which kept the frozen recipe in shell or
batch variables.  V2 expands those variables, grades the two command arms by
their effective arguments, and accepts equivalent failure guards.
"""

from __future__ import annotations

import ast
import json
import re
import shlex
import sys
from collections.abc import Callable
from pathlib import Path


def _expand_shell(source: str) -> str:
    assignments: dict[str, str] = {}
    logical = re.sub(r"\\\s*\n", " ", source)
    for match in re.finditer(
        r'(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)=(?:"([^"]*)"|\'([^\']*)\'|([^\s#]+))',
        logical,
    ):
        assignments[match.group(1)] = next(
            group for group in match.groups()[1:] if group is not None
        )
    variable = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
    for _ in range(3):
        logical = variable.sub(
            lambda match: assignments.get(
                match.group(1) or match.group(2), match.group(0)
            ),
            logical,
        )
    return logical


def _expand_batch(source: str) -> str:
    assignments = {
        match.group(1).upper(): match.group(2)
        for match in re.finditer(
            r'(?im)^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=([^"\r\n]*)"?\s*$',
            source,
        )
    }
    logical = re.sub(r"\^\s*\r?\n", " ", source)
    variable = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")
    for _ in range(3):
        logical = variable.sub(
            lambda match: assignments.get(match.group(1).upper(), match.group(0)),
            logical,
        )
    return logical


def _commands(source: str) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for line in source.splitlines():
        if "kfold_train.py kfold-train" not in line:
            continue
        tokens = shlex.split(line, posix=True)
        start = tokens.index("kfold-train") + 1
        options: dict[str, str] = {}
        index = start
        while index < len(tokens):
            token = tokens[index]
            if token.startswith("--") and index + 1 < len(tokens):
                options[token] = tokens[index + 1]
                index += 2
            else:
                index += 1
        commands.append(options)
    return commands


def _is_zero(value: str) -> bool:
    try:
        return float(value) == 0
    except ValueError:
        return False


def _is_one(value: str) -> bool:
    try:
        return float(value) == 1
    except ValueError:
        return False


def main(repository: Path) -> int:
    training_path = repository / "src/cli/kfold_train.py"
    training = training_path.read_text(encoding="utf-8")
    shell_path = repository / "scripts/train_factorvae_phase1.sh"
    batch_path = repository / "scripts/train_factorvae_phase1.bat"
    shell = shell_path.read_text(encoding="utf-8") if shell_path.exists() else ""
    batch = batch_path.read_text(encoding="utf-8") if batch_path.exists() else ""
    expanded_shell = _expand_shell(shell)
    expanded_batch = _expand_batch(batch)
    shell_commands = _commands(expanded_shell)
    batch_commands = _commands(expanded_batch)

    def warmup_advances_inside_epoch_loop() -> None:
        tree = ast.parse(training)
        matching = []
        for loop in ast.walk(tree):
            if not (
                isinstance(loop, ast.For)
                and isinstance(loop.target, ast.Name)
                and loop.target.id == "epoch"
            ):
                continue
            calls = [
                node
                for node in ast.walk(loop)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
            ]
            setters = [
                node
                for node in calls
                if node.func.attr == "set_epoch"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "epoch"
                and isinstance(node.args[1], ast.Attribute)
                and node.args[1].attr == "epochs"
            ]
            runners = [node for node in calls if node.func.attr == "run_epoch"]
            if setters and runners and setters[0].lineno < runners[0].lineno:
                matching.append(loop)
        assert matching, "set_epoch(epoch, args.epochs) must precede run_epoch"

    def recipes_match_across_platforms() -> None:
        assert len(shell_commands) >= 2, "POSIX launcher needs two arms"
        assert len(batch_commands) >= 2, "Windows launcher needs two arms"
        required = {
            "--model": "factor_vae",
            "--feature-version": "v2",
            "--static-features": "expanded",
            "--k-folds": "5",
            "--purge-left": "3",
            "--purge-right": "65",
            "--batch-size": "4096",
            "--z-dim": "8",
        }
        for platform, commands in (
            ("POSIX", shell_commands),
            ("Windows", batch_commands),
        ):
            for arm in commands[:2]:
                for option, expected in required.items():
                    assert arm.get(option) == expected, (
                        f"{platform} {option}={arm.get(option)!r}, "
                        f"expected {expected!r}"
                    )
        parity_options = (
            "--model",
            "--feature-version",
            "--static-features",
            "--loss",
            "--target",
            "--temperature",
            "--k-folds",
            "--purge-left",
            "--purge-right",
            "--start-date",
            "--end-date",
            "--seq-len",
            "--batch-size",
            "--epochs",
            "--lr",
            "--weight-decay",
            "--seed",
            "--z-dim",
        )
        for arm_index in (0, 1):
            assert {
                option: shell_commands[arm_index].get(option)
                for option in parity_options
            } == {
                option: batch_commands[arm_index].get(option)
                for option in parity_options
            }, f"platform recipe mismatch in arm {arm_index + 1}"

    def main_and_inert_control_arms() -> None:
        for platform, commands in (
            ("POSIX", shell_commands),
            ("Windows", batch_commands),
        ):
            assert len(commands) >= 2, f"{platform} launcher needs two arms"
            main_arm, control_arm = commands[:2]
            assert main_arm.get("--kl-beta") == "0.5"
            assert _is_one(main_arm.get("--lambda-recon", ""))
            assert main_arm.get("--kl-warmup-epochs") == "10"
            assert main_arm.get("--prior-warmup-epochs") == "5"
            assert _is_zero(control_arm.get("--kl-beta", ""))
            assert _is_zero(control_arm.get("--lambda-recon", ""))
            assert _is_zero(control_arm.get("--kl-warmup-epochs", ""))
            assert _is_zero(control_arm.get("--prior-warmup-epochs", ""))

    def failure_stops_later_arm() -> None:
        assert "--main-only" in shell
        assert "--main-only" in batch
        shell_first = expanded_shell.index("kfold_train.py kfold-train")
        shell_second = expanded_shell.index(
            "kfold_train.py kfold-train", shell_first + 1
        )
        shell_between = expanded_shell[shell_first:shell_second]
        assert (
            "set -e" in shell[:shell_first]
            or re.search(r"\|\|[\s{(]*.*\bexit\b", shell_between, re.DOTALL)
        ), "POSIX main failure does not stop control"
        assert shell_between.index("--main-only") > 0
        assert re.search(
            r"--main-only[\s\S]{0,500}\b(exit|return)\b", shell_between
        ), "POSIX main-only guard does not exit before control"

        batch_first = expanded_batch.index("kfold_train.py kfold-train")
        batch_second = expanded_batch.index(
            "kfold_train.py kfold-train", batch_first + 1
        )
        batch_between = expanded_batch[batch_first:batch_second]
        assert re.search(
            r"(?:if\s+errorlevel\s+1[\s\S]{0,160}(?:goto|exit\s+/b)"
            r"|errorlevel[\s\S]{0,240}\bif\b[\s\S]{0,160}exit\s+/b)",
            batch_between,
            re.IGNORECASE,
        ), "Windows main failure does not stop control"
        assert re.search(
            r"--main-only[\s\S]{0,500}(?:goto\s+:done|exit\s+/b)",
            batch_between,
            re.IGNORECASE,
        ), "Windows main-only guard does not skip control"

    return _score(
        "BR-12",
        [
            ("set_epoch_before_each_kfold_training_epoch", 50, warmup_advances_inside_epoch_loop),
            ("cross_platform_recipe_parity", 20, recipes_match_across_platforms),
            ("main_and_prior_only_control_arms", 20, main_and_inert_control_arms),
            ("failure_and_main_only_control_flow", 10, failure_stops_later_arm),
        ],
    )


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
        + json.dumps(
            {"earned": earned, "possible": 100, "criteria": outcomes},
            sort_keys=True,
        )
    )
    if earned == 100:
        print(f"{task} independent acceptance passed")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
