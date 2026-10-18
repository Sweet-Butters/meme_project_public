"""Mock-only tests for tiktok_creative."""
from __future__ import annotations

import pytest

from crawlers import tiktok_creative


FAKE_HASHTAG_PAYLOAD = {
    "data": {
        "list": [
            {
                "rank": 1,
                "rank_diff": 2,
                "hashtag_name": "fyp",
                "hashtag_id": "1234",
                "publish_cnt": 5_000_000,
                "view_cnt": 9_000_000_000,
                "industry_info": {"name": "Entertainment"},
                "country_code": "KR",
            },
            {
                "rank": 2,
                "rank_diff": -1,
                "hashtag_name": "케이팝",
                "hashtag_id": "5678",
                "publish_cnt": 1_000_000,
                "view_cnt": 800_000_000,
                "industry_info": {"name": "Music"},
                "country_code": "KR",
            },
        ]
    }
}

FAKE_SONG_PAYLOAD = {
    "data": {
        "list": [
            {
                "rank": 1,
                "rank_diff": 0,
                "clip_id": "song1",
                "title": "Test Song",
                "author": "Artist X",
                "duration": 30,
                "post_cnt": 200_000,
                "if_original": False,
                "url": "https://...",
            }
        ]
    }
}


def test_crawl_parked_writes_unavailable_snapshot(monkeypatch, tmp_state, read_snapshot):
    # Default state is parked → no network, a "parked" snapshot, empty lists.
    def boom(*a, **k):
        raise AssertionError("_fetch must NOT be called while parked")

    monkeypatch.setattr(tiktok_creative, "_fetch", boom)

    result = tiktok_creative.crawl(period=7, country_code="KR")

    assert result["status"] == "parked"
    assert result["unavailable_reason"]
    assert result["hashtags"] == [] and result["songs"] == []
    assert result["hashtag_count"] == 0 and result["song_count"] == 0

    snap = read_snapshot(tmp_state / "tiktok_creative")
    assert snap["_meta"]["source"] == "tiktok_creative"
    assert snap["status"] == "parked"


def test_crawl_writes_both_lists(monkeypatch, tmp_state, read_snapshot):
    # Exercise the live path (kept for revival) by un-parking for this test.
    monkeypatch.setattr(tiktok_creative, "PARKED", False)

    def fake_fetch(url, params, timeout=30):
        if url == tiktok_creative.HASHTAG_URL:
            return FAKE_HASHTAG_PAYLOAD
        if url == tiktok_creative.SONG_URL:
            return FAKE_SONG_PAYLOAD
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(tiktok_creative, "_fetch", fake_fetch)

    result = tiktok_creative.crawl(period=7, country_code="KR", limit=50)

    assert result["hashtag_count"] == 2
    assert result["song_count"] == 1
    assert result["hashtags"][0]["hashtag_name"] == "fyp"
    assert result["hashtags"][1]["industry"] == "Music"
    assert result["songs"][0]["title"] == "Test Song"
    assert result["songs"][0]["use_count"] == 200_000

    snap = read_snapshot(tmp_state / "tiktok_creative")
    assert snap["_meta"]["source"] == "tiktok_creative"
    assert snap["period_days"] == 7


def test_crawl_rejects_invalid_period(monkeypatch, tmp_state):
    with pytest.raises(ValueError, match="period"):
        tiktok_creative.crawl(period=14)


def test_normalize_handles_alt_field_names():
    # Some responses use 'name'/'id' instead of 'hashtag_name'/'hashtag_id'
    alt = {"rank": 5, "name": "alt", "id": "x", "video_cnt": 100, "view_count": 500}
    h = tiktok_creative._normalize_hashtag(alt)
    assert h["hashtag_name"] == "alt"
    assert h["hashtag_id"] == "x"
    assert h["publish_count"] == 100
    assert h["view_count"] == 500
