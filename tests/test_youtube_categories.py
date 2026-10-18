"""Mock-only tests for youtube_categories — no real API calls."""
from __future__ import annotations

from crawlers import youtube_categories


FAKE_VIDEOS = [
    {"video_id": "a", "title": "AI 코딩 강의 ChatGPT", "tags": ["AI", "코딩", "개발"], "view_count": 1000},
    {"video_id": "b", "title": "AI 뉴스 요약", "tags": ["AI", "뉴스"], "view_count": 500},
]


def test_aggregate_tags_ranks_by_video_count():
    tags = youtube_categories._aggregate_tags(FAKE_VIDEOS)
    by = {t["tag"]: t for t in tags}
    assert by["AI"]["videos"] == 2  # appears in both videos
    assert by["코딩"]["videos"] == 1
    assert tags[0]["tag"] == "AI"  # most-used tag ranked first
    assert by["AI"]["view_sum"] == 1500


def test_aggregate_title_keywords_keeps_short_alnum_drops_stopwords():
    kws = youtube_categories._aggregate_title_keywords(
        [{"title": "AI official MV 코딩 강의", "view_count": 1}]
    )
    words = {k["keyword"] for k in kws}
    assert "AI" in words          # 2-char alnum survives (matters for AI)
    assert "코딩" in words
    assert all(k["keyword"].lower() not in ("official", "mv") for k in kws)


def test_iso8601_to_seconds():
    assert youtube_categories._iso8601_to_seconds("PT3M22S") == 202
    assert youtube_categories._iso8601_to_seconds("PT45S") == 45
    assert youtube_categories._iso8601_to_seconds("PT1H2M3S") == 3723
    assert youtube_categories._iso8601_to_seconds(None) is None
    assert youtube_categories._iso8601_to_seconds("garbage") is None


def test_crawl_builds_all_categories_and_isolates_errors(
    monkeypatch, tmp_state, read_snapshot
):
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")

    def fake_search(api_key, region_code, published_after,
                    video_category_id=None, query=None, video_duration=None,
                    max_results=50, timeout=30):
        if query == "뉴스":   # simulate the news category failing (both forms)
            raise RuntimeError("quotaExceeded")
        return ["v1", "v2"]

    def fake_details(api_key, video_ids, timeout=30):
        return list(FAKE_VIDEOS)

    monkeypatch.setattr(youtube_categories, "_search_video_ids", fake_search)
    monkeypatch.setattr(youtube_categories, "_fetch_video_details", fake_details)

    result = youtube_categories.crawl(region_code="KR", days=7)
    cats = result["categories"]

    assert set(cats) == set(youtube_categories.DEFAULT_CATEGORIES)
    assert result["forms"] == ["short", "long"]
    # Every category is split into short + long sub-results.
    for c in cats.values():
        assert "short" in c and "long" in c

    # news errored → both forms recorded + empty, but run continued
    assert cats["news"]["short"]["video_count"] == 0
    assert "error" in cats["news"]["short"]
    assert any(e.startswith("news/") for e in result["errors"])
    # AI succeeded for both forms (two seeds dedupe to the same 2 ids)
    assert cats["ai"]["mechanism"] == "search"
    assert cats["ai"]["short"]["video_count"] == 2
    assert cats["ai"]["long"]["video_count"] == 2
    assert cats["ai"]["short"]["top_tags"][0]["tag"] == "AI"
    assert cats["ai"]["short"]["top_videos"][0]["view_count"] == 1000  # sorted

    snap = read_snapshot(tmp_state / "youtube_categories")
    assert snap["_meta"]["source"] == "youtube_categories"
    assert "categories" in snap
