"""Cross-source confidence via Reciprocal Rank Fusion (RRF).

The existing trend_score combines per-source signals with a flat
multiplicative bonus per extra source. That handles intensity but says
nothing about *agreement* — a keyword that ranks #1 in one source and #200
in another should be less trustworthy than one that ranks top-10 in three.

RRF (Cormack, Clarke & Buettcher 2009) is the standard fix:

    RRF(kw) = Σ_s  1 / (k + rank_s(kw))     with k = 60

Each source contributes proportionally to its rank, capped by k so a single
high-rank source can't dominate. Missing sources contribute nothing, no
imputation needed. We expose three flavors of the signal:

  confidence_score  — normalized RRF (0-1), our main column
  n_sources_present — how many sources had this keyword at all
  n_sources_top10   — how many sources had it in their own top 10

`confidence_score` is the right primary signal for sales/marketing
prioritization; the integer counts make the explanation auditable.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from crawlers._common import state_dir, write_snapshot


RRF_K = 60         # Cormack et al. standard
TOP10_RANK = 10    # rank threshold for the n_sources_top10 counter


def _latest_synth(source: str = "synth_hot_keywords") -> dict[str, Any] | None:
    d = state_dir(source)
    files = sorted(d.glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def _rank_within_source(keywords: list[dict[str, Any]], src: str) -> dict[str, int]:
    """Rank keywords descending by their raw value in `src`.

    Keywords whose `raw` doesn't include `src` are excluded — RRF will treat
    them as absent and that's what we want.
    """
    scored: list[tuple[str, float]] = []
    for kw in keywords:
        name = kw.get("keyword")
        raw = kw.get("raw") or {}
        if not name or src not in raw:
            continue
        scored.append((name, float(raw[src])))
    scored.sort(key=lambda x: x[1], reverse=True)
    return {name: i + 1 for i, (name, _) in enumerate(scored)}


def compute(synth: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compute RRF / confidence for every keyword in the latest synth snapshot."""
    synth = synth if synth is not None else _latest_synth()
    if not synth:
        return {"keywords": [], "rrf_k": RRF_K}

    keywords = synth.get("keywords") or []
    sources = synth.get("sources_used") or []

    rank_per_source = {src: _rank_within_source(keywords, src) for src in sources}

    results: list[dict[str, Any]] = []
    for kw in keywords:
        name = kw.get("keyword")
        if not name:
            continue

        rrf = 0.0
        n_top10 = 0
        per_source_rank: dict[str, int] = {}
        for src in sources:
            rank = rank_per_source[src].get(name)
            if rank is None:
                continue
            rrf += 1.0 / (RRF_K + rank)
            per_source_rank[src] = rank
            if rank <= TOP10_RANK:
                n_top10 += 1

        results.append({
            "keyword": name,
            "rrf_raw": rrf,
            "n_sources_present": len(per_source_rank),
            "n_sources_top10": n_top10,
            "rank_per_source": per_source_rank,
        })

    # Normalize RRF to [0,1] over today's keyword universe.
    max_rrf = max((r["rrf_raw"] for r in results), default=0.0) or 1.0
    for r in results:
        r["confidence_score"] = round(r["rrf_raw"] / max_rrf, 3)
        del r["rrf_raw"]

    results.sort(key=lambda r: (-r["confidence_score"], -r["n_sources_top10"]))

    return {
        "rrf_k": RRF_K,
        "top10_threshold": TOP10_RANK,
        "sources_used": sources,
        "total_keywords": len(results),
        "keywords": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-source confidence (RRF)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print summary, don't write a snapshot.")
    args = parser.parse_args()

    payload = compute()
    if not args.dry_run:
        target = write_snapshot("confidence", payload)
        print(f"confidence → {target}")
    print(f"sources_used={payload.get('sources_used')}  total={payload.get('total_keywords')}")
    for r in payload["keywords"][:10]:
        print(f"  conf={r['confidence_score']:.3f}  top10={r['n_sources_top10']}  "
              f"present={r['n_sources_present']}  {r['keyword']}")


if __name__ == "__main__":
    main()
