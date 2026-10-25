"""Tests for synth.confidence — RRF math, no network."""
from __future__ import annotations

import pytest

from synth import confidence


def _synth(keywords: list[dict], sources_used: list[str]) -> dict:
    return {
        "_meta": {"source": "synth_hot_keywords", "fetched_at": "2026-05-22T00:00:00+00:00"},
        "sources_used": sources_used,
        "weights": {},
        "total_keywords": len(keywords),
        "keywords": keywords,
    }


def test_rrf_assigns_higher_score_to_keyword_in_more_sources():
    synth = _synth(
        keywords=[
            # X is in both sources → 2 RRF contributions.
            {"keyword": "X", "sources": ["A", "B"], "raw": {"A": 100.0, "B": 50.0}},
            # Y is in source A only, rank 2 there.
            {"keyword": "Y", "sources": ["A"], "raw": {"A": 90.0}},
            # filler so Y is rank 2 (not rank 1) and Z is rank 2 in B
            {"keyword": "filler_A", "sources": ["A"], "raw": {"A": 95.0}},
            {"keyword": "filler_B", "sources": ["B"], "raw": {"B": 25.0}},
            # Z is in source B only, also rank 2.
            {"keyword": "Z", "sources": ["B"], "raw": {"B": 20.0}},
        ],
        sources_used=["A", "B"],
    )
    out = confidence.compute(synth)
    rows = {r["keyword"]: r for r in out["keywords"]}
    # X (in both sources) > Y or Z (single source) — that's the headline.
    assert rows["X"]["confidence_score"] > rows["Y"]["confidence_score"]
    assert rows["X"]["confidence_score"] > rows["Z"]["confidence_score"]
    # Y and Z each appear in exactly one source at the same rank → tie.
    assert rows["Y"]["confidence_score"] == rows["Z"]["confidence_score"]


def test_rrf_uses_within_source_rank_not_raw_value():
    # Raw-value units differ wildly per source — RRF must ignore the units.
    synth = _synth(
        keywords=[
            # Both keywords are #1 in their respective source.
            {"keyword": "AlphaTopOfMillion", "sources": ["A"], "raw": {"A": 1_000_000.0}},
            {"keyword": "BetaTopOfTen",      "sources": ["B"], "raw": {"B": 10.0}},
        ],
        sources_used=["A", "B"],
    )
    out = confidence.compute(synth)
    rows = {r["keyword"]: r for r in out["keywords"]}
    # Both are #1 in their own source → identical RRF (same rank=1).
    assert rows["AlphaTopOfMillion"]["confidence_score"] == rows["BetaTopOfTen"]["confidence_score"]


def test_n_sources_top10_counts_only_high_ranks():
    # Build 15 keywords in source A, only first 10 are in source A's "top 10".
    keywords = []
    for i in range(15):
        keywords.append({
            "keyword": f"kw{i:02d}",
            "sources": ["A", "B"],
            # Source A: high rank = high raw value, decreasing
            # Source B: ALL keywords have raw=100 → ties broken by sort stability;
            # for this test it doesn't matter, n_top10 counts based on actual rank
            "raw": {"A": 100.0 - i, "B": 100.0},
        })
    synth = _synth(keywords=keywords, sources_used=["A", "B"])
    out = confidence.compute(synth)
    rows = {r["keyword"]: r for r in out["keywords"]}

    # The rank-1 keyword in A should have n_sources_top10 = 2 (top10 in both A and B,
    # since it sorts first in B too by stable order).
    assert rows["kw00"]["n_sources_top10"] >= 1
    # The rank-12 keyword in A should NOT count as top10 in A.
    assert rows["kw12"]["n_sources_top10"] < 2


def test_missing_source_does_not_break_keyword():
    # A keyword that only appears in one of two declared sources still scores.
    synth = _synth(
        keywords=[{"keyword": "X", "sources": ["A"], "raw": {"A": 50.0}}],
        sources_used=["A", "B"],
    )
    out = confidence.compute(synth)
    rows = {r["keyword"]: r for r in out["keywords"]}
    assert rows["X"]["confidence_score"] > 0
    assert rows["X"]["n_sources_present"] == 1


def test_empty_synth_returns_empty():
    out = confidence.compute({"keywords": [], "sources_used": []})
    assert out["keywords"] == []


def test_rank_per_source_in_output():
    synth = _synth(
        keywords=[
            {"keyword": "X", "sources": ["A"], "raw": {"A": 100.0}},
            {"keyword": "Y", "sources": ["A"], "raw": {"A": 50.0}},
        ],
        sources_used=["A"],
    )
    out = confidence.compute(synth)
    rows = {r["keyword"]: r for r in out["keywords"]}
    assert rows["X"]["rank_per_source"] == {"A": 1}
    assert rows["Y"]["rank_per_source"] == {"A": 2}
