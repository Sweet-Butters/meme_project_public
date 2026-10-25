"""YouTube trending crawler.

Pulls the official "Most Popular" chart per region + category via the
YouTube Data API v3 (`videos.list?chart=mostPopular`). Costs 1 quota unit
per call; default daily quota is 10,000 units, so this can run hourly per
region with massive headroom.

Categories (Korea-relevant subset):
  10 Music, 17 Sports, 20 Gaming, 22 People & Blogs, 23 Comedy,
  24 Entertainment, 25 News & Politics, 26 Howto & Style,
  27 Education, 28 Science & Technology

If category_ids=None, fetches the global chart (no category filter).

Run directly:
    python -m crawlers.youtube_trending
"""
from __future__ import annotations

import argparse
from typing import Any

import requests
from auto_project.youtube import categories as yt_categories

from ._common import require_env, write_snapshot

API_BASE = "https://www.googleapis.com/youtube/v3/videos"

# Curated KR-trending category subset from auto_project (single source of
# truth — shared with Notes_project). Only used when categories are passed
# explicitly; the default crawl uses the region-wide chart below.
DEFAULT_CATEGORIES_KR: list[str] = [str(i) for i in yt_categories.KR_TRENDING_IDS]

# Bucket key for the region-wide (no-category) chart in videos_by_category.
GLOBAL_CHART_KEY = "all"


def _fetch_chart(
    api_key: str,
    region_code: str,
    category_id: str | None,
    max_results: int = 50,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """One Data API call. Returns the raw `items` list (or [] on category-not-applicable).

    YouTube returns 400 for some (region, category) combos where the chart
    is empty (e.g., News in regions where the API does not surface it). We
    treat that as "no data" rather than a crawler failure.
    """
    params: dict[str, str | int] = {
        "key": api_key,
        "part": "snippet,statistics,contentDetails",
        "chart": "mostPopular",
        "regionCode": region_code,
        "maxResults": max_results,
    }
    if category_id is not None:
        params["videoCategoryId"] = category_id

    resp = requests.get(API_BASE, params=params, timeout=timeout)
    if resp.status_code == 400 and category_id is not None:
        # YouTube returns 400 for empty (region, category) combos. Log it
        # so a total-blank crawl is debuggable, but treat as "no data".
        body = resp.text[:200]
        print(f"  [skip] category={category_id} 400: {body}", flush=True)
        return []
    if resp.status_code != 200:
        # Any other non-200 (403 quota, 403 key restriction, 5xx) should
        # surface clearly with body, not be hidden behind raise_for_status.
        body = resp.text[:400]
        raise RuntimeError(
            f"YouTube API {resp.status_code} for category={category_id} region={region_code}: {body}"
        )
    return resp.json().get("items", [])


def _slim(item: dict[str, Any]) -> dict[str, Any]:
    """Pick only the fields we want to persist."""
    sn = item.get("snippet", {}) or {}
    st = item.get("statistics", {}) or {}
    cd = item.get("contentDetails", {}) or {}
    return {
        "video_id": item.get("id"),
        "title": sn.get("title"),
        "channel_id": sn.get("channelId"),
        "channel_title": sn.get("channelTitle"),
        "category_id": sn.get("categoryId"),
        "published_at": sn.get("publishedAt"),
        "tags": sn.get("tags", []) or [],
        "duration": cd.get("duration"),
        "view_count": int(st["viewCount"]) if "viewCount" in st else None,
        "like_count": int(st["likeCount"]) if "likeCount" in st else None,
        "comment_count": int(st["commentCount"]) if "commentCount" in st else None,
    }


def crawl(
    region_code: str = "KR",
    category_ids: list[str] | None = None,
    max_per_category: int = 50,
) -> dict[str, Any]:
    """Top-level crawler entry. Writes one JSON snapshot under state/youtube_trending/.

    Returns the payload dict that was written.
    """
    env = require_env("YOUTUBE_API_KEY")
    api_key = env["YOUTUBE_API_KEY"]

    by_category: dict[str, list[dict[str, Any]]] = {}
    if category_ids is None:
        # Region-wide "most popular" chart — NO category filter. This is the
        # only mostPopular variant that reliably returns data for every region.
        # Category-filtered charts (videoCategoryId=…) 400 for KR and most
        # non-US regions, which the old per-category default silently swallowed
        # → every snapshot came back with 0 videos. One global call = the real
        # top ~50 trending videos for the region.
        items = _fetch_chart(api_key, region_code, None, max_results=max_per_category)
        by_category[GLOBAL_CHART_KEY] = [_slim(i) for i in items]
        requested = [GLOBAL_CHART_KEY]
    else:
        for cat in category_ids:
            items = _fetch_chart(api_key, region_code, cat, max_results=max_per_category)
            by_category[cat] = [_slim(i) for i in items]
        requested = category_ids

    payload = {
        "region_code": region_code,
        "categories_requested": requested,
        "videos_by_category": by_category,
        "total_videos": sum(len(v) for v in by_category.values()),
    }
    target = write_snapshot("youtube_trending", payload)
    payload["_written_to"] = str(target)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube trending crawler")
    parser.add_argument("--region", default="KR", help="ISO region code (default: KR)")
    parser.add_argument(
        "--categories",
        nargs="*",
        default=None,
        help="YouTube videoCategoryId list (default: KR-relevant subset)",
    )
    parser.add_argument("--max-per-category", type=int, default=50)
    args = parser.parse_args()

    result = crawl(args.region, args.categories, args.max_per_category)
    print(f"Wrote {result['total_videos']} videos across "
          f"{len(result['categories_requested'])} categories to {result['_written_to']}")


if __name__ == "__main__":
    main()
