"""Load and query Zubot JSON config files."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("config/config.json")
_CONFIG_CACHE: dict[Path, tuple[int, dict[str, Any]]] = {}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    """Resolve config path against repo root.

    Priority:
    1. explicit function argument
    2. `ZUBOT_CONFIG_PATH` environment variable
    3. default `config/config.json`
    """
    raw_path: str | Path | None = config_path or os.getenv("ZUBOT_CONFIG_PATH")
    candidate = Path(raw_path) if raw_path else DEFAULT_CONFIG_PATH
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    return candidate.resolve()


def load_config(config_path: str | Path | None = None, *, use_cache: bool = True) -> dict[str, Any]:
    """Load config JSON as a dictionary."""
    resolved = resolve_config_path(config_path)
    if not resolved.exists():
        raise FileNotFoundError(f"Config file not found: {resolved}")

    mtime_ns = resolved.stat().st_mtime_ns
    if use_cache and resolved in _CONFIG_CACHE:
        cached_mtime_ns, cached_payload = _CONFIG_CACHE[resolved]
        if cached_mtime_ns == mtime_ns:
            return cached_payload

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in config file: {resolved}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Config root must be a JSON object: {resolved}")

    _CONFIG_CACHE[resolved] = (mtime_ns, payload)
    return payload


def clear_config_cache() -> None:
    """Clear in-memory config cache."""
    _CONFIG_CACHE.clear()


def get_timezone(config: dict[str, Any] | None = None) -> str | None:
    payload = config or load_config()
    value = payload.get("timezone")
    return value if isinstance(value, str) else None


def get_home_location(config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    payload = config or load_config()
    value = payload.get("home_location")
    return value if isinstance(value, dict) else None


def get_model_by_alias(alias: str, config: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Return `(model_id, model_payload)` for a unique alias."""
    payload = config or load_config()
    models = payload.get("models")
    if not isinstance(models, dict):
        raise ValueError("Config models must be a JSON object keyed by model id.")

    matches: list[tuple[str, dict[str, Any]]] = []
    for model_id, model in models.items():
        if isinstance(model, dict) and model.get("alias") == alias:
            matches.append((model_id, model))

    if not matches:
        raise ValueError(f"No model found for alias '{alias}'.")
    if len(matches) > 1:
        raise ValueError(f"Alias '{alias}' is not unique across models.")
    return matches[0]


def get_default_model(config: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Return default model resolved from `default_model_alias`."""
    payload = config or load_config()
    alias = payload.get("default_model_alias")
    if not isinstance(alias, str) or not alias:
        raise ValueError("Config requires non-empty string `default_model_alias`.")
    return get_model_by_alias(alias, config=payload)


def get_model_by_id(model_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return model payload for a model id."""
    payload = config or load_config()
    models = payload.get("models")
    if not isinstance(models, dict):
        raise ValueError("Config models must be a JSON object keyed by model id.")

    model = models.get(model_id)
    if not isinstance(model, dict):
        raise ValueError(f"Model id '{model_id}' is not defined.")
    return model


def get_model_config(model_ref: str | None = None, config: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Resolve model config by id, alias, or default alias when model_ref is None."""
    payload = config or load_config()
    if model_ref is None:
        return get_default_model(payload)

    models = payload.get("models")
    if isinstance(models, dict) and isinstance(models.get(model_ref), dict):
        return model_ref, models[model_ref]

    return get_model_by_alias(model_ref, payload)


def get_provider_config(provider_name: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return provider config from `model_providers`."""
    payload = config or load_config()
    providers = payload.get("model_providers")
    if not isinstance(providers, dict):
        raise ValueError("Config model_providers must be a JSON object.")

    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        raise ValueError(f"Model provider '{provider_name}' is not defined.")
    return provider
