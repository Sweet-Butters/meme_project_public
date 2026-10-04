"""TikTok Creative Center — trending hashtags + sounds.  **PARKED (2026-05).**

⚠️ STATUS: parked / not collecting. TikTok migrated Creative Center to the
"TikTok One Creative Suite" and locked trending data behind authentication.
There is currently NO viable way to collect it for a public, unattended cron:

  - Old endpoints `creativecenter.tiktok.com/web/api/v1/popular_trend/*/list`
    → HTTP 404 (deprecated).
  - New `ads.tiktok.com/creative_radar_api/v1/popular_trend/*/list` exist but
    return `{"code":40101,"msg":"no permission"}` without a JS-computed request
    signature (anti-bot).
  - Old deep-links redirect to `ads.tiktok.com/creative/creativeCenter/trends`
    ("TikTok One"), a SPA that shows NO trending data unauthenticated (login
    wall) — verified with a headless browser: zero data API calls fire, only
    telemetry. So even Playwright can't reach it without a logged-in account.

Decision (with the user): do NOT pursue authenticated scraping — it needs a
real TikTok account in CI (ToS / ban / 2FA / bot-detection / fragility). TikTok
is parked; the other platforms (Google Trends, YouTube, Naver) carry the
multi-platform signal. See `synth.tierlists` + the dashboard `/tiers` page.

The fetch/parse helpers below are kept for a future revival: if a viable
source appears, implement it and flip ``PARKED = False``. `crawl()` currently
short-circuits to a "parked" snapshot (no network), keeping the time-series
continuous and the reason on record.

Run directly:
    python -m crawlers.tiktok_creative --period 7 --country KR
"""
from __future__ import annotations

import argparse
from typing import Any

import requests

from ._common import write_snapshot

HASHTAG_URL = "https://creativecenter.tiktok.com/web/api/v1/popular_trend/hashtag/list"
SONG_URL = "https://creativecenter.tiktok.com/web/api/v1/popular_trend/song/list"

# Flip to False (and implement a working source) to revive collection. While
# True, crawl() writes a "parked" snapshot without any network call.
PARKED = True
PARKED_REASON = (
    "TikTok moved Creative Center to 'TikTok One Creative Suite' and gated "
    "trending data behind login (old API 404; new creative_radar_api needs a "
    "signed, authenticated session). No public/unattended source available — "
    "parked, not collecting."
)

# A normal-looking browser UA helps avoid 403s; this UI isn't behind real auth
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://ads.tiktok.com/business/creativecenter/",
}


def _fetch(url: str, params: dict[str, Any], timeout: int = 30) -> dict[str, Any] | None:
    """Return parsed JSON, or None if the endpoint is dead / blocked.

    TikTok's Creative Center has no public API contract — they change paths
    without warning. Rather than crash the whole crawl pipeline when that
    happens, we return None and let crawl() write an empty-with-error
    snapshot so synth + downstream steps still run.
    """
    try:
        resp = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        # 404 / 403 / 429 — endpoint changed, blocked, or rate-limited
        return {"_fetch_error": f"HTTP {e.response.status_code} {url}"}
    except (requests.RequestException, ValueError) as e:
        # connection error, JSON decode error
        return {"_fetch_error": f"{type(e).__name__}: {e}"}


def _normalize_hashtag(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": entry.get("rank"),
        "rank_diff": entry.get("rank_diff"),
        "hashtag_name": entry.get("hashtag_name") or entry.get("name"),
        "hashtag_id": entry.get("hashtag_id") or entry.get("id"),
        "publish_count": entry.get("publish_cnt") or entry.get("video_cnt"),
        "view_count": entry.get("view_cnt") or entry.get("view_count"),
        "industry": (entry.get("industry_info") or {}).get("name")
                    or entry.get("industry"),
        "country_code": entry.get("country_code"),
    }


def _normalize_song(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": entry.get("rank"),
        "rank_diff": entry.get("rank_diff"),
        "song_id": entry.get("clip_id") or entry.get("id"),
        "title": entry.get("title") or entry.get("name"),
        "author": entry.get("author") or entry.get("singer"),
        "duration": entry.get("duration"),
        "use_count": entry.get("post_cnt") or entry.get("use_cnt"),
        "is_original": entry.get("if_original"),
        "url": entry.get("url"),
    }


def crawl(
    period: int = 7,
    country_code: str = "KR",
    limit: int = 50,
) -> dict[str, Any]:
    """Pull trending hashtags + sounds for one period/country.

    Parked (see module docstring): writes a "parked" snapshot and makes no
    network call while ``PARKED`` is True.
    """
    if period not in (7, 30, 120):
        raise ValueError("period must be 7, 30, or 120")

    if PARKED:
        payload = {
            "period_days": period,
            "country_code": country_code,
            "limit": limit,
            "status": "parked",
            "unavailable_reason": PARKED_REASON,
            "hashtags": [],
            "songs": [],
            "errors": [],
            "hashtag_count": 0,
            "song_count": 0,
        }
        target = write_snapshot("tiktok_creative", payload)
        payload["_written_to"] = str(target)
        return payload

    common = {"page": 1, "limit": limit, "period": period, "country_code": country_code}

    hashtag_raw = _fetch(HASHTAG_URL, common) or {}
    song_raw = _fetch(SONG_URL, common) or {}

    hashtag_err = hashtag_raw.get("_fetch_error")
    song_err = song_raw.get("_fetch_error")
    errors = [e for e in (hashtag_err, song_err) if e]

    hashtags = [_normalize_hashtag(e) for e in (hashtag_raw.get("data") or {}).get("list", [])]
    songs = [_normalize_song(e) for e in (song_raw.get("data") or {}).get("list", [])]

    payload = {
        "period_days": period,
        "country_code": country_code,
        "limit": limit,
        "hashtags": hashtags,
        "songs": songs,
        "errors": errors,
        "hashtag_count": len(hashtags),
        "song_count": len(songs),
    }
    target = write_snapshot("tiktok_creative", payload)
    payload["_written_to"] = str(target)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="TikTok Creative Center crawler")
    parser.add_argument("--period", type=int, choices=[7, 30, 120], default=7)
    parser.add_argument("--country", default="KR")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    result = crawl(period=args.period, country_code=args.country, limit=args.limit)
    print(f"{result['hashtag_count']} hashtags + {result['song_count']} songs "
          f"({args.country}, {args.period}d) → {result['_written_to']}")


if __name__ == "__main__":
    main()
