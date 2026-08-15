"""Persisted settings and on-disk layout."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .models import PrinterConfig

_LOCK = threading.Lock()


def data_dir() -> Path:
    """Directory holding config.json, templates.json and captured raw dumps."""
    override = os.environ.get("RECIFI_DATA")
    if override:
        path = Path(override)
    else:
        path = Path.home() / ".local" / "share" / "recifi"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def load_config() -> PrinterConfig:
    path = config_path()
    if not path.exists():
        return PrinterConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PrinterConfig()
    # Unknown keys from an older/newer build are ignored rather than fatal.
    known = {k: v for k, v in raw.items() if k in PrinterConfig.model_fields}
    try:
        return PrinterConfig(**known)
    except ValueError:
        return PrinterConfig()


def save_config(cfg: PrinterConfig) -> PrinterConfig:
    with _LOCK:
        tmp = config_path().with_suffix(".tmp")
        tmp.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(config_path())
    return cfg
