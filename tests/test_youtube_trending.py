"""Mock-only tests for youtube_trending — no real API calls."""
from __future__ import annotations

import json

import pytest

from crawlers import youtube_trending
from crawlers._common import MissingEnvError


FAKE_ITEM = {
    "id": "abc123",
    "snippet": {
        "title": "테스트 영상",
        "channelId": "UCxxxx",
        "channelTitle": "테스트 채널",
        "categoryId": "24",
        "publishedAt": "2026-05-14T10:00:00Z",
        "tags": ["meme", "한국"],
    },
    "statistics": {
        "viewCount": "1234567",
        "likeCount": "89000",
        "commentCount": "4500",
    },
    "contentDetails": {"duration": "PT3M22S"},
}


def test_crawl_writes_snapshot(monkeypatch, tmp_state, read_snapshot):
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")

    def fake_fetch(api_key, region_code, category_id, max_results=50, timeout=30):
        assert api_key == "fake-key"
        return [FAKE_ITEM]

    monkeypatch.setattr(youtube_trending, "_fetch_chart", fake_fetch)

    result = youtube_trending.crawl(region_code="KR", category_ids=["24"])

    assert result["region_code"] == "KR"
    assert result["total_videos"] == 1
    assert result["videos_by_category"]["24"][0]["title"] == "테스트 영상"
    assert result["videos_by_category"]["24"][0]["view_count"] == 1234567

    snapshot = read_snapshot(tmp_state / "youtube_trending")
    assert snapshot["_meta"]["source"] == "youtube_trending"
    assert snapshot["videos_by_category"]["24"][0]["video_id"] == "abc123"


def test_crawl_default_uses_global_chart(monkeypatch, tmp_state):
    # The default crawl (no categories) must hit the region-wide chart with
    # NO category filter — exactly ONE call with category_id=None. The old
    # per-category default 400'd on KR and produced empty snapshots.
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")

    calls = []

    def fake_fetch(api_key, region_code, category_id, max_results=50, timeout=30):
        calls.append(category_id)
        return [FAKE_ITEM]

    monkeypatch.setattr(youtube_trending, "_fetch_chart", fake_fetch)

    result = youtube_trending.crawl(region_code="KR")  # no category_ids

    assert calls == [None]  # single global call, not one-per-category
    assert result["categories_requested"] == [youtube_trending.GLOBAL_CHART_KEY]
    assert result["total_videos"] == 1
    assert (
        result["videos_by_category"][youtube_trending.GLOBAL_CHART_KEY][0]["title"]
        == "테스트 영상"
    )


def test_crawl_requires_api_key(monkeypatch, tmp_state):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(MissingEnvError, match="YOUTUBE_API_KEY"):
        youtube_trending.crawl()


def test_slim_handles_missing_statistics():
    minimal = {"id": "x", "snippet": {"title": "t"}}
    s = youtube_trending._slim(minimal)
    assert s["video_id"] == "x"
    assert s["view_count"] is None
    assert s["like_count"] is None
