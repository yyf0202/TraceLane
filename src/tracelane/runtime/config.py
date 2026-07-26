"""Load a private local runtime configuration.

The v0.2 hosted runtime reads connection parameters (including the API key)
from a private JSON file that stays on the local machine and is ignored by
Git.  This module resolves that file into an :class:`HttpRuntimeConfig`.  The
key is used only to authenticate requests; it is never written into traces,
manifests, or artifacts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from tracelane.runtime.http import HttpRuntimeConfig

_DEFAULT_PATH = Path(".local/runtime.json")


def load_http_runtime_config(
    path: str | Path | None = None,
    *,
    model_override: str | None = None,
) -> HttpRuntimeConfig:
    """Resolve an :class:`HttpRuntimeConfig` from a local JSON config file."""
    config_path = Path(path) if path is not None else _DEFAULT_PATH
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"runtime config not found: {config_path}. "
            "Copy configs/runtime/openai-compatible.example.json and set your api_key."
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"runtime config is not valid JSON: {config_path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError("runtime config must be a JSON object")

    base_url = value.get("base_url")
    api_key = value.get("api_key")
    model = model_override or value.get("default_model")
    if not isinstance(model, str) or not model.strip():
        models = value.get("models")
        if isinstance(models, list) and models and isinstance(models[0], str):
            model = models[0]
    timeout = value.get("timeout_seconds", 120.0)
    retries = value.get("max_retries", 2)

    return HttpRuntimeConfig(
        base_url=str(base_url),
        api_key=str(api_key),
        model_id=str(model),
        timeout_seconds=float(timeout),
        max_retries=int(retries),
    )
