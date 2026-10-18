"""Tests for analytics.leadlag — CCF math + interpretation."""
from __future__ import annotations

import datetime as _dt
import json
import math

import pytest

from analytics import leadlag
from crawlers._common import state_dir


# --- statistical primitives -----------------------------------------------

def test_pearson_perfect_positive():
    rho = leadlag._pearson([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0])
    assert rho is not None
    assert math.isclose(rho, 1.0, abs_tol=1e-9)


def test_pearson_perfect_negative():
    rho = leadlag._pearson([1.0, 2.0, 3.0, 4.0], [40.0, 30.0, 20.0, 10.0])
    assert rho is not None
    assert math.isclose(rho, -1.0, abs_tol=1e-9)


def test_pearson_constant_y_returns_none():
    assert leadlag._pearson([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]) is None


def test_pearson_mismatched_length_returns_none():
    assert leadlag._pearson([1.0, 2.0], [1.0, 2.0, 3.0]) is None


# --- cross-correlation ----------------------------------------------------

def test_cross_correlate_peaks_at_known_lag():
    # Construct y = x shifted +2 days: y(t) = x(t-2).
    # So X leads Y by 2 days → CCF should peak at lag=+2.
    x = [0.0, 0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0]
    y = [0.0, 0.0, 0.0, 0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    ccf = leadlag.cross_correlate(x, y, max_lag=5)
    valid = {k: v for k, v in ccf.items() if v is not None}
    best = max(valid, key=lambda k: abs(valid[k]))
    assert best == 2  # X leads Y by 2


def test_cross_correlate_symmetric_at_lag_0():
    # Same series → peak at lag 0, ρ = 1.
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    ccf = leadlag.cross_correlate(x, x, max_lag=3)
    assert math.isclose(ccf[0], 1.0, abs_tol=1e-9)


def test_cross_correlate_clamps_max_lag():
    # Series too short for the requested lag → caller still gets back a
    # dict; entries beyond series length are simply omitted.
    x = [1.0, 2.0]
    y = [1.0, 2.0]
    ccf = leadlag.cross_correlate(x, y, max_lag=10)
    # max_lag is clamped to n-1 = 1
    assert set(ccf.keys()) == {-1, 0, 1}


def test_cross_correlate_empty_series():
    assert leadlag.cross_correlate([], [], max_lag=5) == {}


# --- end-to-end with state fixture ----------------------------------------

def _write_synth(day: _dt.date, raw_per_kw: dict[str, dict[str, float]]) -> None:
    """Synthesize a synth_hot_keywords snapshot for a given day.

    `raw_per_kw[keyword] = {source: value}` — what intensity each source
    reported for that keyword on that day.
    """
    d = state_dir("synth_hot_keywords")
    ts = _dt.datetime(day.year, day.month, day.day, 12, 0, 0,
                      tzinfo=_dt.timezone.utc)
    fname = f"{day.isoformat()}T12-00-00+00-00.json"
    keywords = []
    for kw, raw in raw_per_kw.items():
        keywords.append({
            "keyword": kw,
            "trend_score": sum(raw.values()),
            "sources": list(raw),
            "raw": raw,
        })
    payload = {
        "_meta": {"source": "synth_hot_keywords", "fetched_at": ts.isoformat()},
        "sources_used": sorted({s for raw in raw_per_kw.values() for s in raw}),
        "keywords": keywords,
    }
    (d / fname).write_text(json.dumps(payload), encoding="utf-8")


def test_find_lead_insufficient_data(tmp_state):
    # Only 2 overlapping non-zero days < MIN_OVERLAP (7) → refuse.
    base = _dt.date(2026, 5, 1)
    for i in range(2):
        _write_synth(base + _dt.timedelta(days=i),
                     {"AI": {"tiktok_creative": 50.0,
                             "pytrends_sector": 30.0}})

    res = leadlag.find_lead(
        "AI", "tiktok_creative", "pytrends_sector",
        base, base + _dt.timedelta(days=1),
    )
    assert res["insufficient_data"] is True
    assert "need ≥7" in res["reason"]


def test_find_lead_detects_lead_direction(tmp_state):
    # Construct: TikTok rises first, pytrends rises 2 days later.
    base = _dt.date(2026, 5, 1)
    # 14 days for safe overlap.
    for i in range(14):
        tt_val = 10.0 * (i + 1)  # rising series
        # pytrends mirrors tiktok with a 2-day lag.
        py_val = 10.0 * max(i - 1, 0)
        _write_synth(base + _dt.timedelta(days=i),
                     {"AI": {"tiktok_creative": tt_val,
                             "pytrends_sector": py_val}})

    res = leadlag.find_lead(
        "AI", "tiktok_creative", "pytrends_sector",
        base, base + _dt.timedelta(days=13),
    )
    assert res["insufficient_data"] is False
    assert res["best_lag"] >= 1  # TikTok leads — positive lag
    assert "tiktok_creative LEADS" in res["direction"]
    assert res["best_rho"] > 0.8


def test_find_lead_strength_thresholds(tmp_state):
    # Pure noise — correlation should be weak.
    import random
    rng = random.Random(0)
    base = _dt.date(2026, 5, 1)
    for i in range(14):
        _write_synth(base + _dt.timedelta(days=i),
                     {"AI": {"tiktok_creative": rng.random() * 100,
                             "pytrends_sector": rng.random() * 100}})

    res = leadlag.find_lead(
        "AI", "tiktok_creative", "pytrends_sector",
        base, base + _dt.timedelta(days=13),
    )
    assert res["insufficient_data"] is False
    # Strength label must reflect the |ρ| we observed.
    if abs(res["best_rho"]) >= leadlag.STRONG_THRESHOLD:
        assert res["strength"] == "strong"
    elif abs(res["best_rho"]) >= leadlag.WEAK_THRESHOLD:
        assert res["strength"] == "moderate"
    else:
        assert res["strength"] == "weak"


def test_pairwise_matrix_skeleton(tmp_state):
    # Empty state — every unordered pair should be insufficient.
    sources = ["a", "b", "c"]
    res = leadlag.pairwise_lag_matrix(
        "AI", sources, _dt.date(2026, 5, 1), _dt.date(2026, 5, 7),
    )
    assert res["pairs_computed"] == 0
    # Counts only the upper-triangle pairs (a-b, a-c, b-c). Mirror cells
    # reuse the result without re-counting.
    assert res["pairs_insufficient"] == 3
    # Diagonal entries are marked.
    for s in sources:
        assert res["matrix"][f"{s}|{s}"]["diagonal"] is True
    # Every off-diagonal cell carries the insufficient_data flag (mirrored
    # cells inherit it from the upper-triangle compute).
    for a in sources:
        for b in sources:
            if a == b:
                continue
            assert res["matrix"][f"{a}|{b}"]["insufficient_data"] is True


def test_pairwise_matrix_mirror_is_anti_symmetric(tmp_state):
    # If A leads B by k, then B "leads" A by -k.
    base = _dt.date(2026, 5, 1)
    for i in range(14):
        a_val = 10.0 * (i + 1)
        b_val = 10.0 * max(i - 1, 0)
        _write_synth(base + _dt.timedelta(days=i),
                     {"AI": {"tiktok_creative": a_val,
                             "pytrends_sector": b_val}})

    res = leadlag.pairwise_lag_matrix(
        "AI", ["tiktok_creative", "pytrends_sector"],
        base, base + _dt.timedelta(days=13),
    )
    ab = res["matrix"]["tiktok_creative|pytrends_sector"]
    ba = res["matrix"]["pytrends_sector|tiktok_creative"]
    assert ab["best_lag"] == -ba["best_lag"]
    # Same ρ magnitude — it's symmetric for mirrored lag.
    assert ab["best_rho"] == ba["best_rho"]
