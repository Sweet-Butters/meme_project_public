"""Tests for analytics.timeline — synthetic snapshots, no network."""
from __future__ import annotations

import datetime as _dt
import json

from analytics import timeline
from crawlers._common import state_dir


def _write_synth(day: _dt.date, hour: int, keywords: list[dict]) -> None:
    """Drop a synth_hot_keywords snapshot keyed by (day, hour)."""
    d = state_dir("synth_hot_keywords")
    ts = _dt.datetime(day.year, day.month, day.day, hour, 0, 0,
                      tzinfo=_dt.timezone.utc)
    name = f"{day.isoformat()}T{hour:02d}-00-00+00-00.json"
    payload = {
        "_meta": {"source": "synth_hot_keywords",
                  "fetched_at": ts.isoformat()},
        "sources_used": ["pytrends_sector", "naver_datalab"],
        "keywords": keywords,
    }
    (d / name).write_text(json.dumps(payload), encoding="utf-8")


def test_list_snapshots_filters_by_date(tmp_state):
    a = _dt.date(2026, 5, 1)
    b = _dt.date(2026, 5, 5)
    out_of_range = _dt.date(2026, 5, 10)
    _write_synth(a, 12, [])
    _write_synth(b, 12, [])
    _write_synth(out_of_range, 12, [])

    rows = timeline.list_snapshots(a, b, "synth_hot_keywords")
    assert len(rows) == 2
    assert [ts.date() for ts, _ in rows] == [a, b]


def test_list_snapshots_ignores_malformed_filenames(tmp_state):
    d = state_dir("synth_hot_keywords")
    # Valid snapshot.
    _write_synth(_dt.date(2026, 5, 2), 12, [])
    # Garbage filename — must not crash, must not appear.
    (d / "not-a-timestamp.json").write_text(json.dumps({"keywords": []}))

    rows = timeline.list_snapshots(_dt.date(2026, 5, 1),
                                   _dt.date(2026, 5, 5),
                                   "synth_hot_keywords")
    assert len(rows) == 1


def test_keyword_intensity_dense_series_with_zero_fill(tmp_state):
    # AI present on day 1 and 3; missing on day 2 → zero-fill.
    _write_synth(_dt.date(2026, 5, 1), 12,
                 [{"keyword": "AI", "trend_score": 80.0,
                   "sources": ["pytrends_sector"], "raw": {"pytrends_sector": 90.0}}])
    _write_synth(_dt.date(2026, 5, 3), 12,
                 [{"keyword": "AI", "trend_score": 60.0,
                   "sources": ["pytrends_sector"], "raw": {"pytrends_sector": 70.0}}])

    payload = timeline.keyword_intensity("AI",
                                         _dt.date(2026, 5, 1),
                                         _dt.date(2026, 5, 3))
    assert payload["days"] == ["2026-05-01", "2026-05-02", "2026-05-03"]
    assert payload["values"] == [80.0, 0.0, 60.0]
    assert payload["days_with_data"] == 2
    assert payload["snapshots_in_range"] == 2


def test_keyword_intensity_uses_latest_snapshot_per_day(tmp_state):
    # Two snapshots same day; 18:00 should win over 08:00 (matches momentum).
    _write_synth(_dt.date(2026, 5, 1), 8,
                 [{"keyword": "AI", "trend_score": 20.0, "sources": [],
                   "raw": {"pytrends_sector": 20.0}}])
    _write_synth(_dt.date(2026, 5, 1), 18,
                 [{"keyword": "AI", "trend_score": 90.0, "sources": [],
                   "raw": {"pytrends_sector": 90.0}}])

    payload = timeline.keyword_intensity("AI",
                                         _dt.date(2026, 5, 1),
                                         _dt.date(2026, 5, 1))
    assert payload["values"] == [90.0]


def test_keyword_intensity_source_field_picks_raw(tmp_state):
    _write_synth(_dt.date(2026, 5, 1), 12,
                 [{"keyword": "AI", "trend_score": 100.0,
                   "sources": ["pytrends_sector", "naver_datalab"],
                   "raw": {"pytrends_sector": 90.0, "naver_datalab": 50.0}}])

    p_unified = timeline.keyword_intensity("AI",
                                           _dt.date(2026, 5, 1),
                                           _dt.date(2026, 5, 1))
    p_naver = timeline.keyword_intensity("AI",
                                         _dt.date(2026, 5, 1),
                                         _dt.date(2026, 5, 1),
                                         source="naver_datalab")
    p_pytrends = timeline.keyword_intensity("AI",
                                            _dt.date(2026, 5, 1),
                                            _dt.date(2026, 5, 1),
                                            source="pytrends_sector")
    assert p_unified["values"] == [100.0]
    assert p_naver["values"] == [50.0]
    assert p_pytrends["values"] == [90.0]


def test_keyword_intensity_unknown_keyword_returns_zeros(tmp_state):
    _write_synth(_dt.date(2026, 5, 1), 12,
                 [{"keyword": "AI", "trend_score": 80.0, "sources": [],
                   "raw": {}}])
    payload = timeline.keyword_intensity("DoesNotExist",
                                         _dt.date(2026, 5, 1),
                                         _dt.date(2026, 5, 3))
    assert payload["values"] == [0.0, 0.0, 0.0]
    assert payload["days_with_data"] == 0


def test_top_in_range_averages_then_sorts(tmp_state):
    # AI: 80, 100 → mean 90
    # B:  90, 90  → mean 90 (tie, but AI appeared first by alphabetical?)
    # C: only one day, score 100 → mean 100 highest
    _write_synth(_dt.date(2026, 5, 1), 12, [
        {"keyword": "AI", "trend_score": 80.0, "sources": ["s1"]},
        {"keyword": "B",  "trend_score": 90.0, "sources": ["s1"]},
        {"keyword": "C",  "trend_score": 100.0, "sources": ["s1"]},
    ])
    _write_synth(_dt.date(2026, 5, 2), 12, [
        {"keyword": "AI", "trend_score": 100.0, "sources": ["s1", "s2"]},
        {"keyword": "B",  "trend_score": 90.0, "sources": ["s1"]},
    ])
    payload = timeline.top_in_range(_dt.date(2026, 5, 1), _dt.date(2026, 5, 2), k=5)
    assert payload["n_days_with_snapshots"] == 2
    by_kw = {r["keyword"]: r for r in payload["top"]}
    assert by_kw["C"]["mean_score"] == 100.0
    assert by_kw["AI"]["mean_score"] == 90.0
    assert by_kw["B"]["mean_score"] == 90.0
    # AI has 2 sources accumulated across both days.
    assert set(by_kw["AI"]["sources"]) == {"s1", "s2"}


def test_top_in_range_min_days_filter(tmp_state):
    # Single-day "flukes" should be filtered when min_days >= 2.
    _write_synth(_dt.date(2026, 5, 1), 12, [
        {"keyword": "Fluke", "trend_score": 100.0, "sources": []},
        {"keyword": "Steady", "trend_score": 50.0, "sources": []},
    ])
    _write_synth(_dt.date(2026, 5, 2), 12, [
        {"keyword": "Steady", "trend_score": 50.0, "sources": []},
    ])
    payload = timeline.top_in_range(_dt.date(2026, 5, 1),
                                    _dt.date(2026, 5, 2),
                                    k=5, min_days=2)
    keywords = [r["keyword"] for r in payload["top"]]
    assert "Fluke" not in keywords
    assert "Steady" in keywords


def test_top_in_range_empty_when_no_snapshots(tmp_state):
    payload = timeline.top_in_range(_dt.date(2026, 5, 1),
                                    _dt.date(2026, 5, 2))
    assert payload["top"] == []
    assert payload["n_days_with_snapshots"] == 0
