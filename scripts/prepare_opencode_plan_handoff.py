#!/usr/bin/env python3
"""Freeze a plan CLI transcript and prepare the exact OpenCode build handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracelane.coding import build_handoff_prompt, extract_plan_artifact, load_coding_task
from tracelane.contracts import canonical_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--plan-cli", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    task = load_coding_task(json.loads(args.task.read_text(encoding="utf-8")))
    plan = extract_plan_artifact(task, args.plan_cli)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = args.output_dir / "plan.json"
    prompt_path = args.output_dir / "build-prompt.txt"
    plan_path.write_text(canonical_json(plan.to_dict()) + "\n", encoding="utf-8")
    prompt_path.write_text(build_handoff_prompt(task, plan), encoding="utf-8")
    print(
        canonical_json(
            {
                "plan_path": str(plan_path.resolve()),
                "build_prompt_path": str(prompt_path.resolve()),
                "plan_sha256": plan.content_sha256,
                "plan_session_id": plan.plan_session_id,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
