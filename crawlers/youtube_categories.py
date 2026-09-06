"""YouTube per-category trending — top videos + copyable tags / title keywords.

The global "most popular" chart (crawlers.youtube_trending) is dominated by
music/K-pop in KR. This crawler instead pulls the most-viewed *recent* videos
per CATEGORY (news, gaming, AI, finance, …) and aggregates, per category:

  - top videos (title / channel / views / url)
  - top tags        — the creators' own SEO keywords, ranked by how many of
                       the category's top videos use them. Directly reusable.
  - top title keywords — frequent words/phrases in winning titles.

These are the concrete signals for modelling your own content on what's
currently winning in a niche (not "the algorithm" — that's not extractable —
but the inputs it rewards).

Mechanics per category:
  1. search.list  (order=viewCount, regionCode, publishedAfter=last N days,
                   + videoCategoryId OR q)  → recent high-view video IDs.
  2. videos.list  (snippet+statistics on those IDs) → tags + real view counts
     (search.list does not return tags or stats).

Quota: 100 (search) + 1 (videos) units per category ≈ 600u for 6 categories.
Run DAILY (not on the 90-min trending cron).

Run directly:
    python -m crawlers.youtube_categories --region KR --days 7
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
from collections import Counter, defaultdict
from typing import Any

import requests

from ._common import require_env, write_snapshot
from .youtube_trending import _slim  # reuse the video field picker (SSOT)

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# Category key → how to query it. We use Korean SEARCH QUERIES (not
# videoCategoryId): search.list + videoCategoryId returns 0 for KR under these
# filters, and Korean queries surface KR-relevant content (an English "AI"
# query returns global results). `query` is a list — add seeds in one line.
# `video_category_id` is still supported by the crawler for other regions.
DEFAULT_CATEGORIES: dict[str, dict[str, Any]] = {
    "news":          {"label": "뉴스/시사",  "query": ["뉴스"]},
    "gaming":        {"label": "게임",        "query": ["게임"]},
    "entertainment": {"label": "엔터",        "query": ["예능"]},
    # 교육: "강의" pulled game aim-training; "공부"/"입시" → academic content.
    "education":     {"label": "교육",        "query": ["공부", "입시"]},
    # AI: English "AI" pulled global shorts; Korean seeds → KR AI/tech content.
    "ai":            {"label": "AI/기술",     "query": ["인공지능", "챗GPT"]},
    "finance":       {"label": "금융/재테크", "query": ["재테크"]},
}

# Korean (2+) or alphanumeric (2+) tokens — 2+ so "AI" survives.
_TITLE_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")
# Low-signal title words to drop from keyword aggregation.
_TITLE_STOPWORDS = {
    "official", "mv", "video", "feat", "ft", "the", "and", "for", "with",
    "your", "you", "공식", "영상", "채널", "구독", "shorts", "live", "ep",
}


def _search_video_ids(
    api_key: str,
    region_code: str,
    published_after: str,
    video_category_id: str | None = None,
    query: str | None = None,
    video_duration: str | None = None,
    max_results: int = 50,
    timeout: int = 30,
) -> list[str]:
    """One search.list call → recent high-view video IDs for a category/topic.

    `video_duration` ("short" <4m | "medium" 4-20m | "long" >20m) splits the
    short-form (Shorts) pool from long-form — they run on different algorithms.
    """
    params: dict[str, str | int] = {
        "key": api_key,
        "part": "snippet",
        "type": "video",
        "order": "viewCount",
        "regionCode": region_code,
        "publishedAfter": published_after,
        "maxResults": max_results,
    }
    if video_category_id is not None:
        params["videoCategoryId"] = video_category_id
    if query is not None:
        params["q"] = query
    if video_duration is not None:
        params["videoDuration"] = video_duration

    resp = requests.get(SEARCH_URL, params=params, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"YouTube search.list {resp.status_code}: {resp.text[:300]}")
    ids: list[str] = []
    for item in resp.json().get("items", []):
        vid = (item.get("id") or {}).get("videoId")
        if vid:
            ids.append(vid)
    return ids


_ISO_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _iso8601_to_seconds(dur: str | None) -> int | None:
    """'PT3M22S' → 202. None/unparseable → None."""
    if not dur:
        return None
    m = _ISO_DUR_RE.fullmatch(dur)
    if not m:
        return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def _fetch_video_details(
    api_key: str, video_ids: list[str], timeout: int = 30
) -> list[dict[str, Any]]:
    """videos.list (snippet+statistics+contentDetails) on up to 50 IDs →
    slimmed dicts, each annotated with `duration_seconds`."""
    if not video_ids:
        return []
    params = {
        "key": api_key,
        "part": "snippet,statistics,contentDetails",
        "id": ",".join(video_ids[:50]),
        "maxResults": 50,
    }
    resp = requests.get(VIDEOS_URL, params=params, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"YouTube videos.list {resp.status_code}: {resp.text[:300]}")
    out = []
    for item in resp.json().get("items", []):
        v = _slim(item)
        v["duration_seconds"] = _iso8601_to_seconds(v.get("duration"))
        out.append(v)
    return out


def _aggregate_tags(videos: list[dict[str, Any]], top: int = 15) -> list[dict[str, Any]]:
    """Rank tags by how many of the top videos use them (view sum as tiebreak)."""
    count: Counter[str] = Counter()
    views: dict[str, float] = defaultdict(float)
    for v in videos:
        vv = float(v.get("view_count") or 0)
        for tag in {t.strip() for t in (v.get("tags") or []) if t and t.strip()}:
            count[tag] += 1
            views[tag] += vv
    ranked = sorted(count.items(), key=lambda kv: (kv[1], views[kv[0]]), reverse=True)
    return [{"tag": t, "videos": c, "view_sum": int(views[t])} for t, c in ranked[:top]]


def _aggregate_title_keywords(
    videos: list[dict[str, Any]], top: int = 15
) -> list[dict[str, Any]]:
    """Frequent keywords across the category's winning titles."""
    count: Counter[str] = Counter()
    for v in videos:
        seen: set[str] = set()
        for m in _TITLE_TOKEN_RE.finditer(v.get("title") or ""):
            tok = m.group(0)
            low = tok.lower()
            if low in _TITLE_STOPWORDS or low.isdigit():
                continue
            if low in seen:
                continue
            seen.add(low)
            count[tok] += 1
    return [{"keyword": k, "videos": c} for k, c in count.most_common(top)]


# Form name → search.list videoDuration. Short-form (Shorts pool) and
# long-form run on different algorithms, so we collect + rank them separately.
# "long" maps to medium (4-20m) — the bulk of long-form; >20m is a rare tail.
FORMS: dict[str, str] = {"short": "short", "long": "medium"}


def _collect_form(
    api_key: str,
    region_code: str,
    published_after: str,
    cfg: dict[str, Any],
    video_duration: str,
    per_category: int,
    display_top: int,
) -> dict[str, Any]:
    """Search + enrich + aggregate one (category, form) pair."""
    ids: list[str] = []
    for q in (cfg.get("query") or [None]):
        ids.extend(_search_video_ids(
            api_key, region_code, published_after,
            video_category_id=cfg.get("video_category_id"),
            query=q, video_duration=video_duration, max_results=per_category,
        ))
    seen: set[str] = set()
    uniq = [i for i in ids if not (i in seen or seen.add(i))]
    videos = _fetch_video_details(api_key, uniq)
    videos.sort(key=lambda v: float(v.get("view_count") or 0), reverse=True)
    return {
        "video_count": len(videos),
        "top_videos": videos[:display_top],
        "top_tags": _aggregate_tags(videos),
        "top_title_keywords": _aggregate_title_keywords(videos),
    }


def crawl(
    region_code: str = "KR",
    days: int = 7,
    categories: dict[str, dict[str, Any]] | None = None,
    per_category: int = 50,
    display_top: int = 10,
) -> dict[str, Any]:
    """Per-category, per-form (short/long) top videos + tags/title-keywords.

    A failing (category, form) is recorded in `errors` and left empty — the
    rest still produce data.
    """
    env = require_env("YOUTUBE_API_KEY")
    api_key = env["YOUTUBE_API_KEY"]
    cats = categories or DEFAULT_CATEGORIES

    published_after = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    out: dict[str, Any] = {}
    errors: list[str] = []
    for key, cfg in cats.items():
        entry: dict[str, Any] = {
            "label": cfg.get("label", key),
            "mechanism": "category" if cfg.get("video_category_id") else "search",
            "video_category_id": cfg.get("video_category_id"),
            "query": cfg.get("query"),
        }
        for form, vdur in FORMS.items():
            try:
                entry[form] = _collect_form(
                    api_key, region_code, published_after, cfg, vdur,
                    per_category, display_top,
                )
            except Exception as e:  # noqa: BLE001 — record + continue per (cat, form)
                errors.append(f"{key}/{form}: {type(e).__name__}: {str(e)[:120]}")
                entry[form] = {
                    "video_count": 0, "top_videos": [],
                    "top_tags": [], "top_title_keywords": [], "error": errors[-1],
                }
        out[key] = entry

    payload = {
        "region_code": region_code,
        "window_days": days,
        "published_after": published_after,
        "forms": list(FORMS.keys()),
        "categories": out,
        "errors": errors,
    }
    target = write_snapshot("youtube_categories", payload)
    payload["_written_to"] = str(target)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube per-category trending crawler")
    parser.add_argument("--region", default="KR")
    parser.add_argument("--days", type=int, default=7, help="recency window (days)")
    args = parser.parse_args()

    result = crawl(region_code=args.region, days=args.days)
    cats = result["categories"]
    print(f"region={args.region} window={args.days}d → {result['_written_to']}")
    for key, c in cats.items():
        for form in FORMS:
            f = c.get(form, {})
            tags = ", ".join(t["tag"] for t in f.get("top_tags", [])[:5])
            print(f"  {c['label']:<12} [{form:<5}] {f.get('video_count', 0):>2} videos | tags: {tags or '(none)'}")
    if result["errors"]:
        print("errors:", result["errors"])


if __name__ == "__main__":
    main()
