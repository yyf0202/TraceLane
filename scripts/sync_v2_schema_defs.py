from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "src" / "tracelane" / "schemas" / "v2"


def canonical_definition() -> dict[str, object]:
    value = json.loads((SCHEMAS / "artifact-ref.schema.json").read_text(encoding="utf-8"))
    return {key: item for key, item in value.items() if key not in {"$schema", "$id", "title"}}


def rendered(path: Path, definition: dict[str, object]) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    if "artifact_ref" not in value.get("$defs", {}):
        return path.read_text(encoding="utf-8")
    value["$defs"]["artifact_ref"] = definition
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = []
    definition = canonical_definition()
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        output = rendered(path, definition)
        if output != path.read_text(encoding="utf-8"):
            changed.append(path)
            if not args.check:
                path.write_text(output, encoding="utf-8", newline="\n")
    if args.check and changed:
        raise SystemExit("ArtifactRef schema drift: " + ", ".join(p.name for p in changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
