"""Tests for synth.hot_keywords — pure on-disk synthesis, no API."""
from __future__ import annotations

import json
import pathlib

import pytest

from crawlers._common import write_snapshot
from synth import hot_keywords


def _seed_snapshot(state_root: pathlib.Path, source: str, payload: dict) -> None:
    write_snapshot(source, payload)


def test_synthesize_combines_sources(tmp_state, read_snapshot):
    # naver_search_ad: real absolute volumes (KR strongest signal)
    _seed_snapshot(tmp_state, "naver_search_ad", {
        "keywords": [
            {"keyword": "AI",      "monthly_total": 100_000},
            {"keyword": "딥러닝",  "monthly_total":  10_000},
            {"keyword": "리액트",  "monthly_total":   5_000},
        ]
    })
    # tiktok: viral hashtag with same keyword
    _seed_snapshot(tmp_state, "tiktok_creative", {
        "hashtags": [
            {"hashtag_name": "AI", "view_count": 9_000_000_000},
            {"hashtag_name": "댄스", "view_count": 1_000_000_000},
        ],
        "songs": [],
    })

    result = hot_keywords.synthesize()

    assert result["total_keywords"] >= 3
    by_kw = {r["keyword"]: r for r in result["keywords"]}

    # AI appears in BOTH sources → gets cross-source bonus → should be top
    assert "AI" in by_kw
    assert set(by_kw["AI"]["sources"]) == {"naver_search_ad", "tiktok_creative"}
    assert by_kw["AI"]["trend_score"] == 100.0  # rescaled top

    # 딥러닝 only in naver_search_ad → lower than AI
    assert by_kw["딥러닝"]["sources"] == ["naver_search_ad"]
    assert by_kw["딥러닝"]["trend_score"] < by_kw["AI"]["trend_score"]


def test_synthesize_handles_no_snapshots(tmp_state):
    result = hot_keywords.synthesize()
    assert result["total_keywords"] == 0
    assert result["sources_used"] == []


def test_cross_source_bonus_actually_boosts(tmp_state):
    # Keyword X appears in 3 sources at the same normalized value (=1.0)
    # Keyword Y appears in 1 source at the same normalized value
    _seed_snapshot(tmp_state, "naver_search_ad", {
        "keywords": [
            {"keyword": "X", "monthly_total": 100},
            {"keyword": "Y", "monthly_total": 100},
        ]
    })
    _seed_snapshot(tmp_state, "tiktok_creative", {
        "hashtags": [{"hashtag_name": "X", "view_count": 100}],
        "songs": [],
    })
    _seed_snapshot(tmp_state, "naver_datalab", {
        "main": {"results": [{
            "title": "g1",
            "keywords": ["X"],
            "data": [{"period": "x", "ratio": 50}],
        }]},
        "breakdowns": {},
    })

    result = hot_keywords.synthesize()
    by_kw = {r["keyword"]: r for r in result["keywords"]}

    assert len(by_kw["X"]["sources"]) == 3
    assert len(by_kw["Y"]["sources"]) == 1
    # X's trend_score should be the maximum (100) since it has bonus
    assert by_kw["X"]["trend_score"] > by_kw["Y"]["trend_score"]
    # The groupName ("g1") must not leak into the keyword set.
    assert "g1" not in by_kw


def test_naver_datalab_group_expands_to_member_keywords(tmp_state):
    # DataLab returns one time series per group ("g1"), shared by every
    # keyword inside it. The synth must split that mean ratio across every
    # member keyword, not index it under the groupName.
    _seed_snapshot(tmp_state, "naver_datalab", {
        "main": {"results": [{
            "title": "g1",
            "keywords": ["AI", "딥러닝", "ChatGPT"],
            "data": [
                {"period": "2026-05-04", "ratio": 80},
                {"period": "2026-05-11", "ratio": 100},
                {"period": "2026-05-18", "ratio": 60},
            ],
        }]},
        "breakdowns": {},
    })

    result = hot_keywords.synthesize()
    by_kw = {r["keyword"]: r for r in result["keywords"]}

    assert {"AI", "딥러닝", "ChatGPT"} <= by_kw.keys()
    assert "g1" not in by_kw
    # All three share the group's mean ratio (80) as their raw datalab signal.
    for kw in ("AI", "딥러닝", "ChatGPT"):
        assert by_kw[kw]["raw"]["naver_datalab"] == pytest.approx(80.0)


def test_tokens_from_title_extracts_korean_and_alnum():
    tokens = hot_keywords._tokens_from_title("AI 딥러닝 강의 ChatGPT 코드")
    assert "AI" not in tokens  # AI is 2 chars, below the 3-char threshold
    assert "딥러닝" in tokens
    assert "ChatGPT" in tokens
    assert "코드" not in tokens  # 2 chars


def test_naver_datalab_per_keyword_groups_discriminate(tmp_state):
    # New default crawler mode: each keyword is its own DataLab group, so the
    # snapshot carries a distinct series per keyword. The synth must then give
    # each keyword its own raw value and rank them apart — the opposite of the
    # old single-group "dead signal" where all members tied at the group mean.
    _seed_snapshot(tmp_state, "naver_datalab", {
        "main": {"results": [
            {"title": "AI",      "keywords": ["AI"],      "data": [{"period": "p", "ratio": 100}]},
            {"title": "딥러닝",   "keywords": ["딥러닝"],   "data": [{"period": "p", "ratio": 40}]},
            {"title": "ChatGPT", "keywords": ["ChatGPT"], "data": [{"period": "p", "ratio": 10}]},
        ]},
        "breakdowns": {},
    })

    result = hot_keywords.synthesize()
    by_kw = {r["keyword"]: r for r in result["keywords"]}

    assert by_kw["AI"]["raw"]["naver_datalab"] == pytest.approx(100.0)
    assert by_kw["딥러닝"]["raw"]["naver_datalab"] == pytest.approx(40.0)
    assert by_kw["ChatGPT"]["raw"]["naver_datalab"] == pytest.approx(10.0)
    # Distinct values → real ranking, not a 3-way tie.
    assert (
        by_kw["AI"]["trend_score"]
        > by_kw["딥러닝"]["trend_score"]
        > by_kw["ChatGPT"]["trend_score"]
    )


def test_pytrends_realtime_trending_absorbed():
    # realtime_trending is pure organic discovery (terms we never seeded). It
    # has no score, only rank — top of the list must score highest.
    sig = hot_keywords._signals_from_pytrends({
        "by_sector": {},
        "realtime_trending": ["하정우 주식", "롤 아시안 게임", "아이유 사과"],
    })
    assert {"하정우 주식", "롤 아시안 게임", "아이유 사과"} <= sig.keys()
    assert sig["하정우 주식"] > sig["롤 아시안 게임"] > sig["아이유 사과"]


def test_pytrends_realtime_merges_with_rising_query():
    # Same term as both a rising query and a realtime trend → contributions add
    # up under one key (rising 50 + realtime top-of-1-list 100 = 150).
    sig = hot_keywords._signals_from_pytrends({
        "by_sector": {"x": {
            "interest_over_time": {},
            "related_queries": {
                "seed": {"rising": [{"query": "하정우 주식", "value": 50}], "top": []},
            },
        }},
        "realtime_trending": ["하정우 주식"],
    })
    assert sig["하정우 주식"] == pytest.approx(150.0)
