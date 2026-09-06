"""Mock-only tests for youtube_keyword."""
from __future__ import annotations

import pytest

from crawlers import youtube_keyword


SEARCH_FAKE = [
    {"video_id": "v1", "title": "A", "channel": "c1", "channel_id": "uc1",
     "duration": 120, "view_count_ytdlp": 100, "url": "https://...v1"},
    {"video_id": "v2", "title": "B", "channel": "c2", "channel_id": "uc2",
     "duration": 240, "view_count_ytdlp": 50, "url": "https://...v2"},
    {"video_id": "v3", "title": "C", "channel": "c3", "channel_id": "uc3",
     "duration": 60, "view_count_ytdlp": 1000, "url": "https://...v3"},
]

STATS_FAKE = {
    "v1": {"view_count": 5000, "like_count": 100, "comment_count": 10, "published_at": "2026-05-01T00:00:00Z", "category_id": "24", "tags": []},
    "v2": {"view_count": 9_000_000, "like_count": 200_000, "comment_count": 8000, "published_at": "2026-04-30T00:00:00Z", "category_id": "10", "tags": ["music"]},
    "v3": {"view_count": 200, "like_count": 5, "comment_count": 0, "published_at": "2026-05-10T00:00:00Z", "category_id": "22", "tags": []},
}


def test_crawl_enriched_sorts_by_views(monkeypatch, tmp_state, read_snapshot):
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake")
    monkeypatch.setattr(youtube_keyword, "_ytdlp_search", lambda kw, lim: list(SEARCH_FAKE))
    monkeypatch.setattr(youtube_keyword, "_enrich_stats", lambda key, ids, timeout=30: dict(STATS_FAKE))

    result = youtube_keyword.crawl("딥러닝", limit=3)

    assert result["enriched"] is True
    assert result["count"] == 3
    # Sorted by view_count desc: v2 (9M) > v1 (5K) > v3 (200)
    ids_in_order = [v["video_id"] for v in result["videos"]]
    assert ids_in_order == ["v2", "v1", "v3"]

    snap = read_snapshot(tmp_state / "youtube_keyword" / "딥러닝")
    assert snap["keyword"] == "딥러닝"
    assert snap["enriched"] is True


def test_crawl_no_enrich_keeps_ytdlp_order(monkeypatch, tmp_state):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setattr(youtube_keyword, "_ytdlp_search", lambda kw, lim: list(SEARCH_FAKE))

    def _should_not_be_called(*a, **kw):
        raise AssertionError("enrichment should be skipped without API key")
    monkeypatch.setattr(youtube_keyword, "_enrich_stats", _should_not_be_called)

    result = youtube_keyword.crawl("test", limit=3)
    assert result["enriched"] is False
    # yt-dlp order preserved
    assert [v["video_id"] for v in result["videos"]] == ["v1", "v2", "v3"]


def test_crawl_explicit_no_enrich(monkeypatch, tmp_state):
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake")
    monkeypatch.setattr(youtube_keyword, "_ytdlp_search", lambda kw, lim: list(SEARCH_FAKE))

    def _should_not_be_called(*a, **kw):
        raise AssertionError("enrichment should be skipped when enrich=False")
    monkeypatch.setattr(youtube_keyword, "_enrich_stats", _should_not_be_called)

    result = youtube_keyword.crawl("test", limit=3, enrich=False)
    assert result["enriched"] is False
