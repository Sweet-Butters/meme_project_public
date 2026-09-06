"""Tests for synth.momentum — synthetic time series, no network."""
from __future__ import annotations

import datetime as _dt
import json
import pathlib

import pytest

from synth import momentum
from crawlers._common import state_dir


def _synth_snapshot(day: _dt.date, scores: dict[str, float],
                    sources: dict[str, list[str]] | None = None) -> dict:
    """Build a minimal synth_hot_keywords snapshot for a given day."""
    sources = sources or {}
    return {
        "_meta": {
            "source": "synth_hot_keywords",
            # Fix to 12:00 UTC so each day produces one unambiguous file.
            "fetched_at": _dt.datetime(day.year, day.month, day.day, 12, 0,
                                       tzinfo=_dt.timezone.utc).isoformat(),
        },
        "keywords": [
            {"keyword": kw, "trend_score": score, "sources": sources.get(kw, [])}
            for kw, score in scores.items()
        ],
    }


def _write_history(state: pathlib.Path, days: list[tuple[_dt.date, dict[str, float]]],
                   sources: dict[str, list[str]] | None = None) -> None:
    """Persist one synth snapshot per day under state/synth_hot_keywords/."""
    d = state_dir("synth_hot_keywords")
    for day, scores in days:
        snap = _synth_snapshot(day, scores, sources)
        # Filename has to sort by date so _load_history picks the right one
        # when there's only one snapshot per day in these tests.
        fname = f"{day.isoformat()}T12-00-00+00-00.json"
        (d / fname).write_text(json.dumps(snap), encoding="utf-8")


def test_load_history_picks_latest_per_day(tmp_state):
    # Two snapshots same day → the later one wins.
    d = state_dir("synth_hot_keywords")
    day = _dt.date(2026, 5, 22)
    early = _synth_snapshot(day, {"AI": 50.0})
    early["_meta"]["fetched_at"] = "2026-05-22T08:00:00+00:00"
    late = _synth_snapshot(day, {"AI": 90.0})
    late["_meta"]["fetched_at"] = "2026-05-22T18:00:00+00:00"
    (d / "2026-05-22T08-00-00+00-00.json").write_text(json.dumps(early))
    (d / "2026-05-22T18-00-00+00-00.json").write_text(json.dumps(late))

    history = momentum._load_history()
    assert len(history) == 1
    _, snap = history[0]
    assert snap["keywords"][0]["trend_score"] == 90.0


def test_compute_labels_new_when_history_short(tmp_state):
    today = _dt.date(2026, 5, 22)
    _write_history(tmp_state, [
        (today, {"AI": 80.0}),
    ])
    payload = momentum.compute()
    rows = {r["keyword"]: r for r in payload["keywords"]}
    # 1 day of history → falls below MIN_HISTORY (=3) → "🆕 New"
    assert rows["AI"]["label"] == "🆕 New"


def test_compute_breakout_with_accelerating_z(tmp_state):
    # 7 stable days at 20, then a sudden spike to 90 today.
    base = _dt.date(2026, 5, 14)
    days = []
    for i in range(8):
        day = base + _dt.timedelta(days=i)
        # First 7 days stable around 20 (small variance), today = 90
        score = 20.0 + (i % 2) * 2.0 if i < 7 else 90.0
        days.append((day, {"AI": score}))
    _write_history(tmp_state, days)

    payload = momentum.compute()
    ai = next(r for r in payload["keywords"] if r["keyword"] == "AI")
    # z should be large positive; acceleration must be > 0 to qualify
    assert ai["z_score"] >= 2.0
    assert ai["acceleration"] > 0
    assert ai["label"] == "🔥 Breakout"


def test_compute_declining_when_score_drops(tmp_state):
    # 7 days stable around 80, today crashes to 20.
    base = _dt.date(2026, 5, 14)
    days = []
    for i in range(8):
        day = base + _dt.timedelta(days=i)
        score = 80.0 + (i % 2) * 2.0 if i < 7 else 20.0
        days.append((day, {"AI": score}))
    _write_history(tmp_state, days)

    payload = momentum.compute()
    ai = next(r for r in payload["keywords"] if r["keyword"] == "AI")
    assert ai["z_score"] <= -1.0
    assert ai["label"] == "📉 Declining"


def test_min_std_floor_prevents_false_breakout(tmp_state):
    # All-identical past values would give std=0. The noise floor keeps the
    # z-score finite — a small jump from constant should NOT trigger Breakout
    # because effective_std = MIN_STD (5.0) caps z = (today - mean) / 5.
    base = _dt.date(2026, 5, 14)
    days = []
    for i in range(8):
        day = base + _dt.timedelta(days=i)
        score = 50.0 if i < 7 else 54.0   # 4-point bump from a flat baseline
        days.append((day, {"AI": score}))
    _write_history(tmp_state, days)

    payload = momentum.compute()
    ai = next(r for r in payload["keywords"] if r["keyword"] == "AI")
    # (54 - 50) / MIN_STD(5) = 0.8  → below Rising threshold
    assert ai["z_score"] < momentum.Z_RISING
    assert ai["label"] != "🔥 Breakout"


def test_z_score_clamped(tmp_state):
    # Construct a truly absurd spike to confirm Z_CAP holds.
    base = _dt.date(2026, 5, 14)
    days = [(base + _dt.timedelta(days=i), {"AI": 0.0}) for i in range(7)]
    days.append((base + _dt.timedelta(days=7), {"AI": 100.0}))
    _write_history(tmp_state, days)
    payload = momentum.compute()
    ai = next(r for r in payload["keywords"] if r["keyword"] == "AI")
    assert ai["z_score"] <= momentum.Z_CAP


def test_keyword_disappears_treated_as_zero(tmp_state):
    # AI in days 1-6, not in today's snapshot → score_today=0, velocity negative.
    base = _dt.date(2026, 5, 14)
    days = []
    for i in range(7):
        day = base + _dt.timedelta(days=i)
        days.append((day, {"AI": 60.0}))
    days.append((base + _dt.timedelta(days=7), {"OtherKw": 90.0}))
    _write_history(tmp_state, days)

    payload = momentum.compute()
    ai = next(r for r in payload["keywords"] if r["keyword"] == "AI")
    assert ai["score_today"] == 0.0
    assert ai["velocity"] < 0
    # 0 vs baseline 60 → big negative z (clamped at -Z_CAP via MIN_STD)
    assert ai["z_score"] <= momentum.Z_DECLINING


def test_compute_empty_state_returns_empty(tmp_state):
    payload = momentum.compute()
    assert payload["keywords"] == []
    assert payload["history_days_available"] == 0


def test_sources_attached_from_today_snapshot(tmp_state):
    base = _dt.date(2026, 5, 14)
    days = [(base + _dt.timedelta(days=i), {"AI": 50.0}) for i in range(8)]
    _write_history(tmp_state, days,
                   sources={"AI": ["pytrends_sector", "naver_datalab"]})
    payload = momentum.compute()
    ai = next(r for r in payload["keywords"] if r["keyword"] == "AI")
    assert ai["sources"] == ["pytrends_sector", "naver_datalab"]
