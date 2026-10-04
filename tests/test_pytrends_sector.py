"""Mock-only tests for pytrends_sector."""
from __future__ import annotations

from crawlers import pytrends_sector


def test_crawl_assembles_payload(monkeypatch, tmp_state, read_snapshot):
    monkeypatch.setattr(pytrends_sector, "_realtime_trending", lambda geo="KR", retries=3: ["트렌드A", "트렌드B"])

    def fake_youtube_interest(keywords, geo="KR", timeframe="now 7-d", retries=3):
        return {
            "interest_over_time": {kw: [10, 20, 30] for kw in keywords},
            "related_queries": {kw: {"rising": [{"query": f"{kw}_rising", "value": 50}], "top": []} for kw in keywords},
        }

    monkeypatch.setattr(pytrends_sector, "_youtube_interest", fake_youtube_interest)

    sectors = {"tech": ["AI", "ML"]}
    result = pytrends_sector.crawl(geo="KR", sectors=sectors)

    assert result["geo"] == "KR"
    assert result["realtime_trending"] == ["트렌드A", "트렌드B"]
    assert result["by_sector"]["tech"]["interest_over_time"]["AI"] == [10, 20, 30]
    assert result["by_sector"]["tech"]["related_queries"]["AI"]["rising"][0]["query"] == "AI_rising"

    snap = read_snapshot(tmp_state / "pytrends_sector")
    assert snap["_meta"]["source"] == "pytrends_sector"
    assert snap["sectors_requested"] == ["tech"]


def test_crawl_handles_empty_realtime(monkeypatch, tmp_state):
    monkeypatch.setattr(pytrends_sector, "_realtime_trending", lambda geo="KR", retries=3: [])
    monkeypatch.setattr(pytrends_sector, "_youtube_interest",
                        lambda kws, geo="KR", timeframe="now 7-d", retries=3:
                        {"interest_over_time": {}, "related_queries": {}})

    result = pytrends_sector.crawl(geo="KR", sectors={"music": ["bts"]})
    assert result["realtime_trending"] == []
    assert result["by_sector"]["music"]["interest_over_time"] == {}


def test_default_sectors_have_kr_seeds():
    """Sanity: DEFAULT_SECTORS_KR is non-empty and seeds contain Korean."""
    assert "tech" in pytrends_sector.DEFAULT_SECTORS_KR
    assert "music" in pytrends_sector.DEFAULT_SECTORS_KR
    # At least one seed has Korean characters
    flat = [s for seeds in pytrends_sector.DEFAULT_SECTORS_KR.values() for s in seeds]
    assert any(any(0xAC00 <= ord(c) <= 0xD7A3 for c in s) for s in flat)
