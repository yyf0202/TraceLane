"""Delayed-expansion- and assignment-aware BR-12 adjudicator.

V3 treated variable-definition lines containing the CLI recipe as executable
commands and did not expand Windows delayed variables such as ``!COMMON!``.
V4 expands those variables and only parses actual launcher invocations.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import br12_hidden_acceptance_v2 as v2
import br12_hidden_acceptance_v3 as v3


def _expand_shell(source: str) -> str:
    expanded = v3._expand_shell(source)
    return re.sub(
        r"(?ms)^\s*[A-Za-z_][A-Za-z0-9_]*=\(\s*.*?^\s*\)\s*",
        "",
        expanded,
    )


def _expand_batch(source: str) -> str:
    expanded = v3._expand_batch(source)
    assignments = {
        match.group(1).upper(): match.group(2)
        for match in re.finditer(
            r'(?im)^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=([^"\r\n]*)"?\s*$',
            source,
        )
    }
    delayed = re.compile(r"!([A-Za-z_][A-Za-z0-9_]*)!")
    for _ in range(3):
        expanded = delayed.sub(
            lambda match: assignments.get(
                match.group(1).upper(), match.group(0)
            ),
            expanded,
        )
    expanded = re.sub(
        r'(?im)^\s*set\s+"?[A-Za-z_][A-Za-z0-9_]*='
        r'[^"\r\n]*kfold_train\.py kfold-train[^"\r\n]*"?\s*$',
        "",
        expanded,
    )
    return v3._canonical_invocation(expanded)


def _commands(source: str) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for line in source.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if (
            not stripped
            or low.startswith(("set ", "rem ", "echo ", "#"))
            or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=\(", stripped)
        ):
            continue
        tokens = shlex.split(line, posix=True)
        if "kfold-train" not in tokens:
            continue
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


def main(repository: Path) -> int:
    original_shell = v2._expand_shell
    original_batch = v2._expand_batch
    original_commands = v2._commands
    v2._expand_shell = _expand_shell
    v2._expand_batch = _expand_batch
    v2._commands = _commands
    try:
        return v2.main(repository)
    finally:
        v2._expand_shell = original_shell
        v2._expand_batch = original_batch
        v2._commands = original_commands


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
