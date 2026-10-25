"""Tests for the ASCII chart renderers."""
from __future__ import annotations

import datetime as _dt

import pytest

from analytics import _render


def test_sparkline_empty():
    assert _render.sparkline([]) == ""


def test_sparkline_constant_uses_mid_block():
    # All values identical → mid-level block, length=N.
    out = _render.sparkline([5, 5, 5, 5])
    assert len(out) == 4
    assert set(out) == {_render._SPARK_BLOCKS[len(_render._SPARK_BLOCKS) // 2]}


def test_sparkline_min_max_endpoints():
    out = _render.sparkline([0.0, 50.0, 100.0])
    # Lowest input → lowest block, highest → highest.
    assert out[0] == _render._SPARK_BLOCKS[0]
    assert out[-1] == _render._SPARK_BLOCKS[-1]


def test_sparkline_length_matches_input():
    assert len(_render.sparkline([1.0] * 17)) == 17


def test_bar_chart_renders_each_row():
    rows = [(_dt.date(2026, 5, 1), 25.0), (_dt.date(2026, 5, 2), 100.0)]
    out = _render.bar_chart(rows, width=20)
    lines = out.splitlines()
    assert len(lines) == 2
    # The bigger value gets a wider bar.
    assert lines[1].count(_render._BAR_BLOCK) > lines[0].count(_render._BAR_BLOCK)
    # Date label appears.
    assert "05-01" in lines[0]
    assert "05-02" in lines[1]


def test_bar_chart_empty_safe():
    assert _render.bar_chart([]) == "(no data)"


def test_stats_block_full():
    dates = [_dt.date(2026, 5, d) for d in (1, 2, 3, 4, 5)]
    values = [10.0, 20.0, 30.0, 40.0, 90.0]
    s = _render.stats_block(values, dates)
    assert "mean" in s and "median" in s and "std" in s
    assert "peak: 2026-05-05" in s
    # velocity = 90 - 10 = +80, big positive → ↗ rising
    assert "↗ rising" in s
    # presence is 5/5 since all values > 0
    assert "5/5일" in s


def test_stats_block_falling_trend():
    dates = [_dt.date(2026, 5, d) for d in (1, 2, 3)]
    values = [90.0, 50.0, 10.0]
    s = _render.stats_block(values, dates)
    assert "↘ falling" in s


def test_stats_block_flat_trend():
    dates = [_dt.date(2026, 5, d) for d in (1, 2, 3)]
    values = [50.0, 52.0, 51.0]
    s = _render.stats_block(values, dates)
    assert "→ flat" in s


def test_stats_block_empty_safe():
    assert _render.stats_block([], []).strip() == "(no data)"
