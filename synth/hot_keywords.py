"""Cross-source keyword synthesis.

Reads the latest JSON snapshot from each crawler under state/, extracts
keyword-like signals, and merges them into a single ranked table:

    keyword | sources | youtube_views | tiktok_views | kr_search_vol | trend_score

`trend_score` is a normalized 0-100 composite. Per-source signals are
min-max scaled to [0,1] across the keywords present in that source,
then combined with configurable weights (defaults below). Keywords that
appear in multiple sources get a multiplier — cross-platform signal is
the whole point.

This module does no network calls. It only reads the on-disk state, so
it can run cheaply in CI right after each crawler step.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
from collections import defaultdict
from typing import Any

from crawlers._common import state_dir, write_snapshot


# Per-source weights for trend_score. Tune to taste.
DEFAULT_WEIGHTS: dict[str, float] = {
    "naver_search_ad": 1.0,   # real absolute volumes — strongest signal
    "youtube_trending": 0.8,  # official trending chart
    "tiktok_creative":  0.7,  # leading indicator
    "pytrends_sector":  0.5,  # noisy but YouTube-scoped
    "naver_datalab":    0.5,  # relative ratios
}

CROSS_SOURCE_BONUS = 0.15  # +15% per additional source the keyword appears in


# Korean (3+ chars) or alphanumeric tokens (3+ chars). Filters short noise.
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]{3,}")


def _latest_snapshot(source: str) -> dict[str, Any] | None:
    """Read the lexicographically last JSON file under state/<source>/."""
    d = state_dir(source)
    files = sorted(d.glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def _tokens_from_title(title: str) -> set[str]:
    if not title:
        return set()
    return {m.group(0) for m in _TOKEN_RE.finditer(title)}


def _signals_from_youtube_trending(snap: dict[str, Any]) -> dict[str, float]:
    """Extract per-keyword view-count signals from trending titles + tags."""
    bag: dict[str, float] = defaultdict(float)
    for videos in (snap.get("videos_by_category") or {}).values():
        for v in videos:
            views = v.get("view_count") or 0
            for tok in _tokens_from_title(v.get("title") or ""):
                bag[tok] += views
            for tag in v.get("tags") or []:
                bag[tag] += views
    return dict(bag)


def _signals_from_tiktok(snap: dict[str, Any]) -> dict[str, float]:
    bag: dict[str, float] = defaultdict(float)
    for h in snap.get("hashtags") or []:
        name = h.get("hashtag_name")
        if name:
            bag[name] += float(h.get("view_count") or 0)
    return dict(bag)


def _signals_from_naver_search_ad(snap: dict[str, Any]) -> dict[str, float]:
    bag: dict[str, float] = {}
    for row in snap.get("keywords") or []:
        kw = row.get("keyword")
        if kw:
            bag[kw] = float(row.get("monthly_total") or 0)
    return bag


# Realtime-trending entries carry no numeric score, only rank order. Map the
# top entry to this value and decay linearly down the list, so genuinely
# organic "what's hot in KR right now" terms enter the ranking on a scale
# comparable to the 0-100 interest indices.
_REALTIME_TOP_SCORE = 100.0


def _signals_from_pytrends(snap: dict[str, Any]) -> dict[str, float]:
    bag: dict[str, float] = defaultdict(float)
    for sector, payload in (snap.get("by_sector") or {}).items():
        # 1) Mean interest over time per seed
        for kw, series in (payload.get("interest_over_time") or {}).items():
            if series:
                bag[kw] += sum(series) / len(series)
        # 2) Rising related queries
        for kw, blocks in (payload.get("related_queries") or {}).items():
            for r in blocks.get("rising", []) or []:
                q = r.get("query")
                if q:
                    bag[q] += float(r.get("value") or 0)
    # 3) Realtime trending — pure organic discovery (terms we never seeded).
    #    Rank-scored: top of the list scores highest, decaying linearly.
    realtime = [t for t in (snap.get("realtime_trending") or []) if t]
    n = len(realtime)
    for i, title in enumerate(realtime):
        bag[title] += _REALTIME_TOP_SCORE * (n - i) / n
    return dict(bag)


def _signals_from_naver_datalab(snap: dict[str, Any]) -> dict[str, float]:
    # DataLab returns one time series per keyword *group*. With the crawler in
    # its default per-keyword-group mode each group holds exactly one keyword,
    # so each keyword gets its own mean ratio on DataLab's shared 0-100 scale —
    # a discriminating signal. (Legacy single-group snapshots lump several
    # keywords into one group; we still spread the shared mean across members
    # so nothing breaks, but that path can't tell those keywords apart and was
    # the source of the old "everything ties at 1.0" dead signal.)
    bag: dict[str, float] = defaultdict(float)
    for grp in (snap.get("main") or {}).get("results", []) or []:
        keywords = grp.get("keywords") or []
        series = grp.get("data") or []
        if not (keywords and series):
            continue
        mean_ratio = sum(p.get("ratio") or 0 for p in series) / len(series)
        for kw in keywords:
            bag[kw] += mean_ratio
    return dict(bag)


_EXTRACTORS = {
    "youtube_trending": _signals_from_youtube_trending,
    "tiktok_creative": _signals_from_tiktok,
    "naver_search_ad": _signals_from_naver_search_ad,
    "pytrends_sector": _signals_from_pytrends,
    "naver_datalab":   _signals_from_naver_datalab,
}


def _minmax(d: dict[str, float]) -> dict[str, float]:
    if not d:
        return {}
    lo = min(d.values())
    hi = max(d.values())
    if hi == lo:
        return {k: 1.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def synthesize(
    weights: dict[str, float] | None = None,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """Read latest snapshots, build unified ranking, write a synth snapshot.

    Returns the payload, sorted by trend_score desc.
    """
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    sources = sources or list(_EXTRACTORS.keys())

    per_source_raw: dict[str, dict[str, float]] = {}
    for src in sources:
        snap = _latest_snapshot(src)
        if snap is None:
            continue
        extractor = _EXTRACTORS.get(src)
        if extractor is None:
            continue
        signals = extractor(snap)
        if signals:
            per_source_raw[src] = signals

    per_source_norm = {src: _minmax(sig) for src, sig in per_source_raw.items()}

    # Combine
    combined: dict[str, dict[str, Any]] = {}
    for src, norm in per_source_norm.items():
        w = weights.get(src, 0.0)
        raw = per_source_raw[src]
        for kw, n in norm.items():
            row = combined.setdefault(kw, {"keyword": kw, "sources": [], "score": 0.0, "raw": {}})
            row["sources"].append(src)
            row["score"] += w * n
            row["raw"][src] = raw[kw]

    # Cross-source bonus
    for row in combined.values():
        extra = max(0, len(row["sources"]) - 1)
        row["score"] *= (1.0 + CROSS_SOURCE_BONUS * extra)

    rows = sorted(combined.values(), key=lambda r: r["score"], reverse=True)

    # Rescale top score to 100 for readability
    if rows:
        top = rows[0]["score"]
        if top > 0:
            for r in rows:
                r["trend_score"] = round(r["score"] / top * 100, 2)
        else:
            for r in rows:
                r["trend_score"] = 0.0
    for r in rows:
        r.pop("score", None)

    payload = {
        "sources_used": list(per_source_raw.keys()),
        "weights": weights,
        "total_keywords": len(rows),
        "keywords": rows,
    }
    target = write_snapshot("synth_hot_keywords", payload)
    payload["_written_to"] = str(target)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-source keyword synthesis")
    parser.add_argument("--sources", nargs="*", default=None,
                        help="Subset of sources to include (default: all available)")
    args = parser.parse_args()

    result = synthesize(sources=args.sources)
    print(f"Synthesized {result['total_keywords']} keywords from "
          f"{len(result['sources_used'])} sources → {result['_written_to']}")
    for r in result["keywords"][:10]:
        print(f"  {r['trend_score']:>6.2f}  {r['keyword']:<30} {r['sources']}")


if __name__ == "__main__":
    main()
