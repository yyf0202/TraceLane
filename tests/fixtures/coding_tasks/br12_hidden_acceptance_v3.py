"""Array- and module-invocation-aware BR-12 adjudicator.

V2 expanded scalar variables but did not understand POSIX argument arrays or
``python -m src.cli.kfold_train``.  V3 normalizes those equivalent launcher
forms before delegating to the V2 semantic checks.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import br12_hidden_acceptance_v2 as v2

_V2_EXPAND_BATCH = v2._expand_batch


def _canonical_invocation(source: str) -> str:
    return re.sub(
        r"src(?:[./\\])cli(?:[./\\])kfold_train(?:\.py)?\s+kfold-train",
        "kfold_train.py kfold-train",
        source,
    )


def _expand_shell(source: str) -> str:
    logical = re.sub(r"\\\s*\n", " ", source)
    assignments: dict[str, str] = {}
    for match in re.finditer(
        r'(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)=(?!\()'
        r'(?:"([^"]*)"|\'([^\']*)\'|([^\s#]+))',
        logical,
    ):
        assignments[match.group(1)] = next(
            group for group in match.groups()[1:] if group is not None
        )
    variable = re.compile(
        r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|"
        r"([A-Za-z_][A-Za-z0-9_]*))"
    )
    for _ in range(3):
        logical = variable.sub(
            lambda match: assignments.get(
                match.group(1) or match.group(2), match.group(0)
            ),
            logical,
        )

    arrays: dict[str, str] = {}
    for match in re.finditer(
        r"(?ms)^\s*([A-Za-z_][A-Za-z0-9_]*)=\(\s*(.*?)\)",
        logical,
    ):
        arrays[match.group(1)] = " ".join(
            shlex.split(match.group(2), comments=True, posix=True)
        )
    array_ref = re.compile(
        r'"?\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\}"?'
    )
    logical = array_ref.sub(
        lambda match: arrays.get(match.group(1), match.group(0)),
        logical,
    )
    return _canonical_invocation(logical)


def _expand_batch(source: str) -> str:
    return _canonical_invocation(_V2_EXPAND_BATCH(source))


def main(repository: Path) -> int:
    original_shell = v2._expand_shell
    original_batch = v2._expand_batch
    v2._expand_shell = _expand_shell
    v2._expand_batch = _expand_batch
    try:
        return v2.main(repository)
    finally:
        v2._expand_shell = original_shell
        v2._expand_batch = original_batch


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
