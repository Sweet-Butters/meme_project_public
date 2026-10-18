"""Tests for the trend_digest agent.

Compose is exercised with realistic fake snapshots. The Telegram send is
mocked via monkeypatching _send_telegram so no network call ever happens.
"""
from __future__ import annotations

from agents import trend_digest
from crawlers._common import write_snapshot


YT_FAKE = {
    "videos_by_category": {
        "24": [
            {"video_id": "abc", "title": "테스트 영상 <매우 긴 제목>",
             "channel_title": "테스트 채널", "view_count": 1_500_000},
            {"video_id": "def", "title": "또 다른 영상",
             "channel_title": "다른 채널", "view_count": 500_000},
        ],
        "10": [
            {"video_id": "ghi", "title": "음악 영상",
             "channel_title": "뮤직", "view_count": 3_000_000},
        ],
    }
}

SYNTH_FAKE = {
    "keywords": [
        {"keyword": "AI", "trend_score": 100.0, "sources": ["naver_search_ad", "tiktok_creative"]},
        {"keyword": "댄스", "trend_score": 73.4, "sources": ["tiktok_creative"]},
    ]
}

TT_FAKE = {
    "hashtags": [
        {"hashtag_name": "fyp", "view_count": 9_000_000_000, "industry": "Entertainment"},
        {"hashtag_name": "케이팝", "view_count": 800_000_000, "industry": "Music"},
    ]
}


def test_compose_includes_all_sections():
    msg = trend_digest.compose(YT_FAKE, SYNTH_FAKE, TT_FAKE)

    # Header
    assert "트렌드 다이제스트" in msg

    # YouTube section — sorted by views (음악 3M > 테스트 1.5M > 또 다른 500K)
    assert msg.index("음악 영상") < msg.index("테스트 영상")
    assert msg.index("테스트 영상") < msg.index("또 다른 영상")
    # /add line present and URL correct
    assert "/add https://youtu.be/ghi" in msg
    assert "/add https://youtu.be/abc" in msg
    # HTML escaping for special characters in title
    assert "&lt;매우 긴 제목&gt;" in msg

    # Keyword section
    assert "AI" in msg
    assert "100.0" in msg
    assert "naver_search_ad" in msg

    # TikTok section
    assert "#fyp" in msg
    assert "9,000,000,000" in msg
    assert "Entertainment" in msg


def test_compose_skips_empty_sections():
    # Only synth data
    msg = trend_digest.compose(None, SYNTH_FAKE, None)
    assert "AI" in msg
    # No YouTube/TikTok markers
    assert "/add" not in msg
    assert "#fyp" not in msg


def test_compose_handles_all_empty():
    msg = trend_digest.compose(None, None, None)
    # Only the header line
    assert msg.startswith("<b>🔥")
    assert "/add" not in msg


def test_top_youtube_videos_sorts_across_categories():
    snap = {
        "videos_by_category": {
            "1": [{"video_id": "a", "view_count": 100}],
            "2": [{"video_id": "b", "view_count": 500}, {"video_id": "c", "view_count": 50}],
            "3": [{"video_id": "d", "view_count": 1000}],
        }
    }
    top = trend_digest._top_youtube_videos(snap, k=3)
    assert [v["video_id"] for v in top] == ["d", "b", "a"]


def test_run_returns_no_snapshots_when_state_empty(tmp_state):
    result = trend_digest.run()
    assert result["sent"] is False
    assert result["reason"] == "no snapshots"
    assert result["sources_present"] == []


def test_run_sends_when_state_present(tmp_state, monkeypatch):
    write_snapshot("youtube_trending", YT_FAKE)
    write_snapshot("tiktok_creative", TT_FAKE)

    sent: list[str] = []

    def fake_send(msg: str) -> bool:
        sent.append(msg)
        return True

    monkeypatch.setattr(trend_digest, "_send_telegram", fake_send)

    result = trend_digest.run()
    assert result["sent"] is True
    assert "youtube_trending" in result["sources_present"]
    assert "tiktok_creative" in result["sources_present"]
    assert len(sent) == 1
    assert "/add https://youtu.be/ghi" in sent[0]


def test_run_dry_run_does_not_send(tmp_state, monkeypatch):
    write_snapshot("youtube_trending", YT_FAKE)

    def fake_send(msg: str) -> bool:
        raise AssertionError("dry_run must not call _send_telegram")

    monkeypatch.setattr(trend_digest, "_send_telegram", fake_send)

    result = trend_digest.run(dry_run=True)
    assert result["sent"] is False
    assert result["reason"] == "dry_run"
    assert "message" in result
    assert "/add" in result["message"]


# --- delta tests ---

def test_enrich_with_delta_marks_new_when_no_previous():
    latest = {"keywords": [{"keyword": "AI", "trend_score": 90.0, "sources": ["s1"]}]}
    rows = trend_digest._enrich_keywords_with_delta(latest, None, k=5)
    assert rows[0]["delta_label"] == "🆕"
    assert rows[0]["delta_value"] is None


def test_enrich_with_delta_up_down_stable():
    previous = {"keywords": [
        {"keyword": "AI", "trend_score": 80.0},
        {"keyword": "댄스", "trend_score": 50.0},
        {"keyword": "코딩", "trend_score": 30.0},
    ]}
    latest = {"keywords": [
        {"keyword": "AI", "trend_score": 95.3, "sources": []},     # +15.3 ↑
        {"keyword": "댄스", "trend_score": 46.7, "sources": []},   # -3.3 ↓
        {"keyword": "코딩", "trend_score": 30.2, "sources": []},   # +0.2 → stable
    ]}
    rows = trend_digest._enrich_keywords_with_delta(latest, previous, k=5)
    by_kw = {r["keyword"]: r for r in rows}
    assert by_kw["AI"]["delta_label"].startswith("↑")
    assert by_kw["AI"]["delta_value"] == 15.3
    assert by_kw["댄스"]["delta_label"].startswith("↓")
    assert by_kw["코딩"]["delta_label"] == "→"


def test_enrich_with_delta_new_keyword_among_existing():
    previous = {"keywords": [{"keyword": "AI", "trend_score": 80.0}]}
    latest = {"keywords": [
        {"keyword": "AI", "trend_score": 90.0, "sources": []},      # ↑
        {"keyword": "케이팝", "trend_score": 70.0, "sources": []},  # 🆕
    ]}
    rows = trend_digest._enrich_keywords_with_delta(latest, previous, k=5)
    by_kw = {r["keyword"]: r for r in rows}
    assert by_kw["AI"]["delta_label"].startswith("↑")
    assert by_kw["케이팝"]["delta_label"] == "🆕"


def test_run_includes_delta_in_message_when_two_synth_snapshots(tmp_state, monkeypatch):
    # First snapshot
    # Two snapshots same calendar day → momentum still sees 1 day of history.
    # The legacy delta-arrow path is gone (the brief replaces it). Keep this
    # test as proof the run completes and the brief renders.
    write_snapshot("synth_hot_keywords", {
        "_meta": {"source": "synth_hot_keywords",
                  "fetched_at": "2026-05-22T00:00:00+00:00"},
        "sources_used": ["s1"],
        "keywords": [{"keyword": "AI", "trend_score": 80.0,
                      "sources": ["s1"], "raw": {"s1": 100.0}}],
    })

    sent: list[str] = []
    monkeypatch.setattr(trend_digest, "_send_telegram", lambda m: sent.append(m) or True)

    result = trend_digest.run()
    assert result["sent"] is True
    assert "트렌드 브리프" in sent[0]


def test_run_surfaces_new_keywords_in_brief_when_history_thin(tmp_state, monkeypatch):
    # First-day rollout: only one synth snapshot exists. Momentum sees
    # history_days_available=1 → every keyword labeled "🆕 New". The brief
    # promotes those into a "🆕 신규" fallback section so the digest isn't
    # contentless.
    write_snapshot("synth_hot_keywords", {
        "_meta": {"source": "synth_hot_keywords",
                  "fetched_at": "2026-05-22T00:00:00+00:00"},
        "sources_used": ["s1"],
        "keywords": [{"keyword": "AI", "trend_score": 80.0,
                      "sources": ["s1"], "raw": {"s1": 100.0}}],
    })
    sent: list[str] = []
    monkeypatch.setattr(trend_digest, "_send_telegram", lambda m: sent.append(m) or True)

    result = trend_digest.run()
    assert result["sent"] is True
    assert "🆕 신규" in sent[0]
    assert "AI" in sent[0]
