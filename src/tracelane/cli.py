from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracelane",
        description="A trace-first evaluation harness for evidence-grounded agents.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    parser.print_help()
    return 0
