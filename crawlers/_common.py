"""Shared helpers for all crawlers.

Each crawler reads env vars, fetches data, and writes a JSON snapshot to
state/<source>/<isotimestamp>.json. Snapshots are time-series: append, never
overwrite.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
from typing import Any


class MissingEnvError(RuntimeError):
    """Crawler can't run because a required env var is unset."""


def require_env(*names: str) -> dict[str, str]:
    """Return a dict of {name: value} for the given env vars.

    Raises MissingEnvError listing every missing var (so the user sees them
    all at once instead of one at a time).
    """
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise MissingEnvError(
            "Missing required env vars: " + ", ".join(missing)
            + ". See .env.example."
        )
    return {n: os.environ[n] for n in names}


def state_dir(source: str) -> pathlib.Path:
    """Resolve and create state/<source>/ relative to repo root."""
    root = pathlib.Path(os.environ.get("STATE_DIR", "state"))
    path = root / source
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_snapshot(source: str, payload: dict[str, Any]) -> pathlib.Path:
    """Write a JSON snapshot under state/<source>/<UTC-iso>.json.

    Filename uses UTC ISO-8601 with seconds precision, ':' replaced with
    '-' so the path is portable across filesystems.
    """
    ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    safe = ts.replace(":", "-")
    target = state_dir(source) / f"{safe}.json"
    payload = {"_meta": {"source": source, "fetched_at": ts}, **payload}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
