"""Quote-safe BR-12 semantic adjudicator.

V4 attempted to shell-parse every non-comment launcher line before checking
whether it was a training invocation.  Unrelated multiline commands could
therefore crash the grader on an unmatched quote.  V5 only parses candidate
K-fold invocations and treats a malformed candidate as absent.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import br12_hidden_acceptance_v2 as v2
import br12_hidden_acceptance_v4 as v4


def _commands(source: str) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for line in source.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if (
            not stripped
            or low.startswith(("set ", "rem ", "echo ", "#"))
            or "kfold-train" not in line
        ):
            continue
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError:
            continue
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
    v2._expand_shell = v4._expand_shell
    v2._expand_batch = v4._expand_batch
    v2._commands = _commands
    try:
        return v2.main(repository)
    finally:
        v2._expand_shell = original_shell
        v2._expand_batch = original_batch
        v2._commands = original_commands


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
