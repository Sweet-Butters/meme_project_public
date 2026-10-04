"""Per-platform top-N keyword tierlists.

Each platform (Google Trends, YouTube, TikTok, Naver) gets its OWN ranked
top-N list, built from that platform's latest crawl snapshot using the same
per-source signal extractors as ``synth.hot_keywords`` — but kept SEPARATE
instead of merged into the cross-source composite.

Why separate: the composite (synth_hot_keywords / momentum) answers "what's
hot across everything", but for professional use you also want each platform's
independent view — they tell different stories, and persisting each as its own
time-series lets us later compute cross-platform lead/lag ("TikTok leads
YouTube by N days") and per-platform lifecycle without untangling a blended
score.

Each list is written to ``state/tierlist_<platform>/<UTC-iso>.json`` so the
per-platform history accumulates independently. This module does no network
calls — it only reads on-disk state, so it runs cheaply in CI right after the
crawlers.

CLI:
    python -m synth.tierlists            # build + write all platform tierlists
    python -m synth.tierlists --top 10
    python -m synth.tierlists --dry-run  # print, don't write
"""
from __future__ import annotations

import argparse
from typing import Any

from crawlers._common import write_snapshot
# Reuse the exact per-source extractors + snapshot reader the composite uses,
# so a platform's tierlist and its contribution to synth stay consistent.
from synth.hot_keywords import _EXTRACTORS, _latest_snapshot

# Platform label → state source directory. Insertion order = display order.
# Google first (richest organic signal today), Naver last (seed-bound).
PLATFORM_SOURCES: dict[str, str] = {
    "google": "pytrends_sector",
    "youtube": "youtube_trending",
    "tiktok": "tiktok_creative",
    "naver": "naver_datalab",
}

DEFAULT_TOP_N = 10


def build_tierlist(
    platform: str, source: str, top_n: int = DEFAULT_TOP_N
) -> dict[str, Any]:
    """Rank one platform's latest snapshot into a top-N list.

    Returns a payload with rank / keyword / raw score / within-list percent
    (top = 100). Empty ``keywords`` when the platform has no current data
    (e.g. an unconfigured or failing crawler) — the caller still gets a valid
    payload so the time-series stays continuous.
    """
    snap = _latest_snapshot(source)
    extractor = _EXTRACTORS.get(source)
    signals: dict[str, float] = (
        extractor(snap) if (snap is not None and extractor is not None) else {}
    )

    ranked = sorted(signals.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    top_score = ranked[0][1] if ranked else 0.0
    keywords = [
        {
            "rank": i + 1,
            "keyword": kw,
            "score": round(float(v), 4),
            "score_pct": round(v / top_score * 100, 2) if top_score > 0 else 0.0,
        }
        for i, (kw, v) in enumerate(ranked)
    ]
    return {
        "platform": platform,
        "source": source,
        "top_n": top_n,
        "total_candidates": len(signals),
        "keywords": keywords,
    }


def build_all(
    top_n: int = DEFAULT_TOP_N, write: bool = True
) -> dict[str, dict[str, Any]]:
    """Build every platform's tierlist. Writes one snapshot per platform when
    ``write`` is True. Returns {platform: payload}."""
    out: dict[str, dict[str, Any]] = {}
    for platform, source in PLATFORM_SOURCES.items():
        payload = build_tierlist(platform, source, top_n)
        if write:
            target = write_snapshot(f"tierlist_{platform}", payload)
            payload["_written_to"] = str(target)
        out[platform] = payload
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Build per-platform keyword tierlists")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                        help=f"How many keywords per platform (default {DEFAULT_TOP_N})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the tierlists, don't write snapshots.")
    args = parser.parse_args()

    results = build_all(top_n=args.top, write=not args.dry_run)
    for platform, payload in results.items():
        kws = payload["keywords"]
        tail = f" → {payload['_written_to']}" if "_written_to" in payload else ""
        status = f"{len(kws)} kw / {payload['total_candidates']} candidates"
        print(f"{platform:8} ({payload['source']}): {status}{tail}")
        for r in kws[:5]:
            print(f"    {r['rank']:>2}. {r['keyword']}  ({r['score']})")
        if not kws:
            print("     (no data — crawler unconfigured or failing)")


if __name__ == "__main__":
    main()
