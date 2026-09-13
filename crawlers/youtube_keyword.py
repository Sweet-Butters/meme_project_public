"""YouTube keyword → top videos crawler.

Two-stage:
  1. `yt-dlp ytsearchN:<keyword>` — free, no quota, gets video IDs + light
     metadata. Default N=50.
  2. YouTube Data API `videos.list?id=...&part=statistics` — 1 quota unit
     per call (up to 50 IDs per call), enriches with view/like/comment
     counts. This is where "trending around a keyword" actually emerges,
     because yt-dlp's order is relevance, not popularity.

If YOUTUBE_API_KEY is absent, stage 2 is skipped — caller still gets the
yt-dlp output. Useful for keyword discovery without burning quota.

Run directly:
    python -m crawlers.youtube_keyword "딥러닝" --limit 30
"""
from __future__ import annotations

import argparse
import os
from typing import Any

import requests

from ._common import write_snapshot

API_BASE = "https://www.googleapis.com/youtube/v3/videos"


def _ytdlp_search(keyword: str, limit: int) -> list[dict[str, Any]]:
    """Use yt-dlp's Python API. Imported lazily so tests can monkeypatch."""
    from yt_dlp import YoutubeDL

    opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,  # don't recurse into each video; faster
        "no_warnings": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{keyword}", download=False) or {}

    entries = info.get("entries", []) or []
    out: list[dict[str, Any]] = []
    for e in entries:
        if not e:
            continue
        out.append({
            "video_id": e.get("id"),
            "title": e.get("title"),
            "channel": e.get("channel") or e.get("uploader"),
            "channel_id": e.get("channel_id"),
            "duration": e.get("duration"),
            "view_count_ytdlp": e.get("view_count"),
            "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}",
        })
    return out


def _enrich_stats(api_key: str, video_ids: list[str], timeout: int = 30) -> dict[str, dict[str, Any]]:
    """Call videos.list in batches of 50. Returns {video_id: stats_dict}."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        params = {
            "key": api_key,
            "part": "statistics,snippet",
            "id": ",".join(batch),
        }
        resp = requests.get(API_BASE, params=params, timeout=timeout)
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            vid = item.get("id")
            st = item.get("statistics", {}) or {}
            sn = item.get("snippet", {}) or {}
            out[vid] = {
                "view_count": int(st["viewCount"]) if "viewCount" in st else None,
                "like_count": int(st["likeCount"]) if "likeCount" in st else None,
                "comment_count": int(st["commentCount"]) if "commentCount" in st else None,
                "published_at": sn.get("publishedAt"),
                "category_id": sn.get("categoryId"),
                "tags": sn.get("tags", []) or [],
            }
    return out


def crawl(keyword: str, limit: int = 50, enrich: bool = True) -> dict[str, Any]:
    """Search YouTube for `keyword`, enrich with stats, write snapshot.

    Returns the payload dict. Sorts the final list by view_count desc when
    enrichment was successful, so the "hot videos for this keyword" appear
    first.
    """
    rows = _ytdlp_search(keyword, limit)

    if enrich and os.environ.get("YOUTUBE_API_KEY"):
        ids = [r["video_id"] for r in rows if r.get("video_id")]
        stats = _enrich_stats(os.environ["YOUTUBE_API_KEY"], ids)
        for r in rows:
            extra = stats.get(r["video_id"]) or {}
            r.update(extra)
        rows.sort(key=lambda r: (r.get("view_count") or 0), reverse=True)
        enriched = True
    else:
        enriched = False

    payload = {
        "keyword": keyword,
        "limit": limit,
        "enriched": enriched,
        "count": len(rows),
        "videos": rows,
    }
    target = write_snapshot(f"youtube_keyword/{keyword}", payload)
    payload["_written_to"] = str(target)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube keyword → top videos")
    parser.add_argument("keyword", help="Search keyword")
    parser.add_argument("--limit", type=int, default=50, help="Number of results (default 50)")
    parser.add_argument("--no-enrich", action="store_true", help="Skip Data API enrichment")
    args = parser.parse_args()

    result = crawl(args.keyword, args.limit, enrich=not args.no_enrich)
    head = "enriched" if result["enriched"] else "raw yt-dlp"
    print(f"[{head}] {result['count']} videos for '{args.keyword}' → {result['_written_to']}")


if __name__ == "__main__":
    main()
