"""Shared pytest fixtures.

We never call real APIs in tests. Crawlers are designed so the network call
is isolated to a single function (e.g., `_fetch_*`) that tests monkeypatch.
"""
from __future__ import annotations

import json
import pathlib

import pytest


@pytest.fixture
def tmp_state(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Redirect STATE_DIR to a tmp path; return it."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def read_snapshot():
    """Helper: load the only JSON file in a directory and return its dict."""
    def _read(directory: pathlib.Path) -> dict:
        files = sorted(directory.glob("*.json"))
        assert len(files) == 1, f"expected 1 snapshot, found {len(files)} in {directory}"
        return json.loads(files[0].read_text(encoding="utf-8"))
    return _read
