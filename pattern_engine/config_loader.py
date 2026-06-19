"""
config_loader.py — load/override engine thresholds from JSON config files.

Usage:
    cfg = load_config()                      # bundled default_config.json
    cfg = load_config("/path/to/my.json")    # user override (deep-merged)
    cfg.pivots["left_bars"]                  # dotted access via attributes
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("pattern_engine")

_DEFAULT_PATH = Path(__file__).resolve().parent / "config" / "default_config.json"


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class EngineConfig:
    """Thin attribute view over the config dict (each top-level key → attr)."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data
        for key, val in data.items():
            setattr(self, key, val)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def as_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(self._data)


def load_config(path: Optional[str] = None) -> EngineConfig:
    with open(_DEFAULT_PATH) as fh:
        data = json.load(fh)
    if path:
        try:
            with open(path) as fh:
                data = _deep_merge(data, json.load(fh))
        except Exception as exc:
            logger.warning("config override %s failed (%s) — using defaults", path, exc)
    return EngineConfig(data)
