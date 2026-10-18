"""Tests for synth.tierlists — per-platform top-N, pure on-disk, no API."""
from __future__ import annotations

import pytest

from crawlers._common import write_snapshot
from synth import tierlists


def test_build_tierlist_ranks_and_caps(tmp_state):
    # pytrends: AI interest mean = 15; rising 하정우 주식 = 300, 롤 = 100.
    write_snapshot("pytrends_sector", {
        "by_sector": {"s": {
            "interest_over_time": {"AI": [10, 20]},
            "related_queries": {
                "AI": {
                    "rising": [
                        {"query": "하정우 주식", "value": 300},
                        {"query": "롤", "value": 100},
                    ],
                    "top": [],
                },
            },
        }},
        "realtime_trending": [],
    })

    t = tierlists.build_tierlist("google", "pytrends_sector", top_n=2)

    assert t["platform"] == "google"
    assert t["source"] == "pytrends_sector"
    assert t["total_candidates"] == 3  # AI, 하정우 주식, 롤
    # Only top-2 kept, ranked by score desc.
    assert [k["keyword"] for k in t["keywords"]] == ["하정우 주식", "롤"]
    assert [k["rank"] for k in t["keywords"]] == [1, 2]
    # within-list percent: top = 100, next = 100/300.
    assert t["keywords"][0]["score_pct"] == 100.0
    assert t["keywords"][1]["score_pct"] == pytest.approx(33.33, abs=0.01)


def test_youtube_tierlist_ranks_tags_by_views(tmp_state):
    write_snapshot("youtube_trending", {
        "videos_by_category": {"all": [
            {"title": "x", "tags": ["고양이"], "view_count": 1000},
            {"title": "x", "tags": ["강아지"], "view_count": 200},
        ]},
    })

    t = tierlists.build_tierlist("youtube", "youtube_trending", top_n=10)

    by_kw = {k["keyword"]: k for k in t["keywords"]}
    assert by_kw["고양이"]["rank"] == 1
    assert by_kw["고양이"]["score"] == 1000.0
    assert by_kw["강아지"]["rank"] == 2
    assert by_kw["강아지"]["score_pct"] == pytest.approx(20.0)


def test_build_tierlist_empty_when_no_snapshot(tmp_state):
    # No youtube snapshot seeded → valid payload, empty keywords (continuous
    # time-series even when a crawler is unconfigured / failing).
    t = tierlists.build_tierlist("youtube", "youtube_trending", top_n=10)
    assert t["platform"] == "youtube"
    assert t["total_candidates"] == 0
    assert t["keywords"] == []


def test_build_all_covers_every_platform(tmp_state):
    results = tierlists.build_all(write=False)
    assert set(results) == set(tierlists.PLATFORM_SOURCES)
    for platform, payload in results.items():
        assert payload["platform"] == platform
        assert "keywords" in payload
        # write=False → no snapshot written, no _written_to key.
        assert "_written_to" not in payload


def test_build_all_writes_one_snapshot_per_platform(tmp_state, read_snapshot):
    write_snapshot("pytrends_sector", {
        "by_sector": {"s": {
            "interest_over_time": {},
            "related_queries": {"x": {"rising": [{"query": "테스트", "value": 50}], "top": []}},
        }},
        "realtime_trending": [],
    })

    results = tierlists.build_all(write=True)

    # Each platform got its own snapshot directory + a _written_to pointer.
    for platform in tierlists.PLATFORM_SOURCES:
        assert "_written_to" in results[platform]
        snap = read_snapshot(tmp_state / f"tierlist_{platform}")
        assert snap["_meta"]["source"] == f"tierlist_{platform}"
        assert snap["platform"] == platform
    # Google picked up the seeded rising keyword.
    assert results["google"]["keywords"][0]["keyword"] == "테스트"
