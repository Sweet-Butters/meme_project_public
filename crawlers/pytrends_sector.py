"""Google Trends (YouTube property) crawler by sector.

Uses `pytrends` (unofficial Google Trends client) with `gprop='youtube'`
to scope the relative-interest queries to YouTube searches specifically,
not generic Google web search.

Two outputs:
  1. Per-sector trending searches — uses `realtime_trending_searches` for
     the configured region (general Google Trends, not YouTube-scoped, but
     the most popular searches almost always show up on YouTube too).
  2. Per-seed-keyword YouTube interest curves — uses `build_payload` +
     `interest_over_time` with `gprop='youtube'`. Sectors → seed keywords
     are configured below; the crawler reports both the 7-day interest
     curves and the related rising queries per seed.

The 0-100 indices are relative, not absolute volumes. For absolute KR
volumes use crawlers.naver_search_ad.

Caveats:
  - Google Trends rate limits anonymous clients aggressively. Backoff in
    the library kicks in automatically; we retry with sleeps.
  - `realtime_trending_searches` only supports a fixed set of country codes
    (US, KR, JP, GB, ...). KR is supported.

Run directly:
    python -m crawlers.pytrends_sector
"""
from __future__ import annotations

import argparse
import time
from typing import Any

from ._common import write_snapshot

# Sector → seed keywords. Edit this dict to retune what we track.
DEFAULT_SECTORS_KR: dict[str, list[str]] = {
    "tech": ["AI", "딥러닝", "ChatGPT", "코딩"],
    "music": ["케이팝", "뉴진스", "아이유"],
    "gaming": ["롤", "발로란트", "마인크래프트"],
    "food": ["맛집", "다이어트", "레시피"],
    "finance": ["주식", "비트코인", "부동산"],
    "lifestyle": ["브이로그", "운동", "패션"],
}


def _pytrends_client():
    """Lazy import. Tests monkeypatch this."""
    from pytrends.request import TrendReq
    return TrendReq(hl="ko-KR", tz=540)  # tz=540 → KST (UTC+9)


def _realtime_trending(geo: str = "KR", retries: int = 3) -> list[str]:
    """Generic Google Trends realtime trending for the region."""
    client = _pytrends_client()
    for attempt in range(retries):
        try:
            df = client.realtime_trending_searches(pn=geo)
            return df["title"].tolist() if df is not None and not df.empty else []
        except Exception:
            if attempt == retries - 1:
                return []
            time.sleep(2 ** attempt)
    return []


def _youtube_interest(
    keywords: list[str],
    geo: str = "KR",
    timeframe: str = "now 7-d",
    retries: int = 3,
) -> dict[str, Any]:
    """interest_over_time + related_queries with gprop='youtube'."""
    client = _pytrends_client()
    for attempt in range(retries):
        try:
            client.build_payload(keywords, timeframe=timeframe, geo=geo, gprop="youtube")
            iot = client.interest_over_time()
            iot_dict: dict[str, list[int]] = {}
            if iot is not None and not iot.empty:
                for kw in keywords:
                    if kw in iot.columns:
                        iot_dict[kw] = [int(v) for v in iot[kw].tolist()]
            related = client.related_queries() or {}
            related_clean: dict[str, dict[str, list[dict[str, Any]]]] = {}
            for kw, blocks in related.items():
                rising = blocks.get("rising") if blocks else None
                top = blocks.get("top") if blocks else None
                related_clean[kw] = {
                    "rising": rising.to_dict(orient="records") if rising is not None else [],
                    "top": top.to_dict(orient="records") if top is not None else [],
                }
            return {"interest_over_time": iot_dict, "related_queries": related_clean}
        except Exception:
            if attempt == retries - 1:
                return {"interest_over_time": {}, "related_queries": {}, "error": "rate_limited_or_failed"}
            time.sleep(2 ** attempt)
    return {"interest_over_time": {}, "related_queries": {}}


def crawl(
    geo: str = "KR",
    sectors: dict[str, list[str]] | None = None,
    timeframe: str = "now 7-d",
) -> dict[str, Any]:
    """Per-sector + realtime trending crawl. One snapshot written."""
    if sectors is None:
        sectors = DEFAULT_SECTORS_KR

    realtime = _realtime_trending(geo=geo)

    by_sector: dict[str, Any] = {}
    for sector, seeds in sectors.items():
        by_sector[sector] = _youtube_interest(seeds, geo=geo, timeframe=timeframe)

    payload = {
        "geo": geo,
        "timeframe": timeframe,
        "realtime_trending": realtime,
        "by_sector": by_sector,
        "sectors_requested": list(sectors.keys()),
    }
    target = write_snapshot("pytrends_sector", payload)
    payload["_written_to"] = str(target)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="pytrends sector crawler")
    parser.add_argument("--geo", default="KR")
    parser.add_argument("--timeframe", default="now 7-d")
    args = parser.parse_args()

    result = crawl(geo=args.geo, timeframe=args.timeframe)
    print(f"{len(result['sectors_requested'])} sectors + "
          f"{len(result['realtime_trending'])} realtime trends → {result['_written_to']}")


if __name__ == "__main__":
    main()
