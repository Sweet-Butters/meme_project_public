"""Lead/lag analysis between data sources via cross-correlation.

For a given keyword, builds per-source daily time series from the
``synth_hot_keywords`` snapshots (using the ``raw[source]`` column) and
asks: how many days does source A lead source B?

This is a *prediction* tool. If TikTok consistently spikes 2 days before
Naver DataLab for the same keyword, the marketing team can act on TikTok
signals 48 hours earlier than they otherwise could.

Method
------
Standard Pearson cross-correlation function (CCF). For each lag k ∈
[-max_lag, +max_lag], compute the correlation between X(t) and Y(t+k).
The lag at which |ρ| is largest is the *typical* lead time:

  best_lag > 0  →  X leads Y by best_lag days
  best_lag < 0  →  Y leads X by |best_lag| days
  best_lag = 0  →  synchronous

Guardrails
----------
- Require at least ``MIN_OVERLAP`` overlapping non-zero days. Below that,
  results are statistical noise — we return ``insufficient_data=True``
  and skip the interpretation.
- Hard caps on correlation interpretation:
    |ρ| < WEAK_THRESHOLD     → don't even claim a relationship
    |ρ| < STRONG_THRESHOLD   → "moderate" — directional but noisy
    |ρ| ≥ STRONG_THRESHOLD   → "strong" — actionable
- Constant series (variance 0) short-circuits to NaN rather than divide
  by zero.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import math
import sys
from typing import Sequence

from analytics import timeline
from analytics._dateparse import parse_range


MIN_OVERLAP = 7        # minimum overlapping non-zero days to compute CCF
WEAK_THRESHOLD = 0.4
STRONG_THRESHOLD = 0.6
DEFAULT_MAX_LAG = 14


# --- core statistics -------------------------------------------------------

def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Pearson ρ on two equal-length series. None if either is constant."""
    if len(x) != len(y) or len(x) < 2:
        return None
    mx, my = _mean(x), _mean(y)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx2 = sum((xi - mx) ** 2 for xi in x)
    dy2 = sum((yi - my) ** 2 for yi in y)
    if dx2 == 0 or dy2 == 0:
        return None
    return num / math.sqrt(dx2 * dy2)


def cross_correlate(
    x: Sequence[float],
    y: Sequence[float],
    max_lag: int,
) -> dict[int, float | None]:
    """Return ``{lag: ρ}`` for ``lag ∈ [-max_lag, +max_lag]``.

    Positive lag k means we compare X(t) with Y(t+k) — i.e. shifting Y
    forward in time. If ρ peaks at positive k, X is *earlier*: X leads.
    """
    n = min(len(x), len(y))
    if n == 0:
        return {}
    max_lag = min(max_lag, n - 1)
    out: dict[int, float | None] = {}
    for k in range(-max_lag, max_lag + 1):
        if k >= 0:
            xs = x[: n - k]
            ys = y[k: n]
        else:
            xs = x[-k: n]
            ys = y[: n + k]
        out[k] = _pearson(xs, ys)
    return out


# --- per-source intensity extraction --------------------------------------

def _series_for_source(
    keyword: str, source: str,
    start: _dt.date, end: _dt.date,
) -> list[float]:
    """Daily time series of `keyword` from synth's `raw[source]` channel."""
    payload = timeline.keyword_intensity(keyword, start, end, source=source)
    return payload["values"]


# --- analysis entry points -------------------------------------------------

def find_lead(
    keyword: str,
    source_a: str,
    source_b: str,
    start: _dt.date,
    end: _dt.date,
    *,
    max_lag: int = DEFAULT_MAX_LAG,
) -> dict:
    """Lead/lag for one keyword between two sources.

    Returns enough metadata that callers can render a complete report
    *or* embed it inside a larger matrix without re-running the math.
    """
    xa = _series_for_source(keyword, source_a, start, end)
    xb = _series_for_source(keyword, source_b, start, end)

    overlap = sum(1 for a, b in zip(xa, xb) if a > 0 and b > 0)
    if overlap < MIN_OVERLAP:
        return {
            "source_a": source_a, "source_b": source_b,
            "insufficient_data": True,
            "n_samples": len(xa), "overlap_days": overlap,
            "reason": f"need ≥{MIN_OVERLAP} overlapping days, have {overlap}",
        }

    ccf = cross_correlate(xa, xb, max_lag)
    # Pick the lag with the largest |ρ| — but only among lags where ρ is defined.
    valid = [(k, r) for k, r in ccf.items() if r is not None]
    if not valid:
        return {
            "source_a": source_a, "source_b": source_b,
            "insufficient_data": True,
            "n_samples": len(xa), "overlap_days": overlap,
            "reason": "all correlations undefined (constant series?)",
        }
    best_lag, best_rho = max(valid, key=lambda kr: abs(kr[1]))

    strength = (
        "strong"   if abs(best_rho) >= STRONG_THRESHOLD else
        "moderate" if abs(best_rho) >= WEAK_THRESHOLD   else
        "weak"
    )
    direction = (
        f"{source_a} LEADS {source_b} by {best_lag} day(s)"  if best_lag > 0 else
        f"{source_b} LEADS {source_a} by {-best_lag} day(s)" if best_lag < 0 else
        f"{source_a} and {source_b} move synchronously"
    )

    return {
        "source_a": source_a, "source_b": source_b,
        "insufficient_data": False,
        "n_samples": len(xa), "overlap_days": overlap,
        "best_lag": best_lag, "best_rho": round(best_rho, 3),
        "strength": strength, "direction": direction,
        "ccf": {int(k): (round(r, 3) if r is not None else None) for k, r in ccf.items()},
    }


def pairwise_lag_matrix(
    keyword: str,
    sources: Sequence[str],
    start: _dt.date,
    end: _dt.date,
    *,
    max_lag: int = DEFAULT_MAX_LAG,
) -> dict:
    """N×N matrix. Each cell (a, b) holds the lead/lag of a vs b.

    Cells on the diagonal are nulled. The matrix is anti-symmetric in
    ``best_lag`` (lag(a→b) = -lag(b→a)) so we only compute the upper
    triangle and mirror it. Cells where overlap < MIN_OVERLAP carry an
    ``insufficient_data`` flag instead of numbers.
    """
    n = len(sources)
    cells: dict[tuple[str, str], dict] = {}
    pairs_computed = 0
    pairs_insufficient = 0

    for i, a in enumerate(sources):
        for j, b in enumerate(sources):
            if i == j:
                cells[(a, b)] = {"diagonal": True}
                continue
            if i < j:
                res = find_lead(keyword, a, b, start, end, max_lag=max_lag)
                cells[(a, b)] = res
                if res.get("insufficient_data"):
                    pairs_insufficient += 1
                else:
                    pairs_computed += 1
            else:
                # Mirror from the upper triangle.
                mirror = cells[(b, a)]
                if mirror.get("insufficient_data"):
                    cells[(a, b)] = dict(mirror)
                else:
                    cells[(a, b)] = {
                        **mirror,
                        "source_a": a, "source_b": b,
                        "best_lag": -mirror["best_lag"],
                        # Direction flips, ρ is symmetric for mirrored lag.
                        "direction": (
                            f"{a} LEADS {b} by {-mirror['best_lag']} day(s)"
                            if -mirror['best_lag'] > 0 else
                            f"{b} LEADS {a} by {mirror['best_lag']} day(s)"
                            if -mirror['best_lag'] < 0 else
                            f"{a} and {b} move synchronously"
                        ),
                    }

    # Pick strongest non-diagonal cell with sufficient data.
    strongest = None
    for (a, b), cell in cells.items():
        if a == b or cell.get("insufficient_data"):
            continue
        if strongest is None or abs(cell["best_rho"]) > abs(strongest["best_rho"]):
            strongest = cell

    return {
        "keyword": keyword,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sources": list(sources),
        "matrix": {f"{a}|{b}": cell for (a, b), cell in cells.items()},
        "pairs_computed": pairs_computed,
        "pairs_insufficient": pairs_insufficient,
        "strongest": strongest,
    }


# --- rendering -------------------------------------------------------------

def _short_source(name: str) -> str:
    # Compact labels for the matrix grid.
    return (name
            .replace("naver_", "n.")
            .replace("_sector", "")
            .replace("_creative", "")
            .replace("_trending", ""))


def _format_matrix(payload: dict) -> str:
    sources = payload["sources"]
    short = [_short_source(s) for s in sources]
    col_w = max(8, max(len(s) for s in short) + 1)
    label_w = max(len(s) for s in short)

    lines = [
        f"🔄 Lead/Lag — {payload['keyword']}",
        f"  range : {payload['start']} → {payload['end']}",
        f"  pairs : {payload['pairs_computed']} computed, "
        f"{payload['pairs_insufficient']} insufficient",
        "",
        "  positive = row source LEADS column source by N days",
        "  (cells: lag / ρ ; '—' = insufficient data)",
        "",
    ]

    header = " " * (label_w + 4) + "".join(s.center(col_w) for s in short)
    lines.append(header)
    sep = " " * (label_w + 4) + "".join("─" * col_w for _ in short)
    lines.append(sep)

    for a, sa in zip(sources, short):
        row_cells: list[str] = []
        for b in sources:
            cell = payload["matrix"][f"{a}|{b}"]
            if cell.get("diagonal"):
                content = "·"
            elif cell.get("insufficient_data"):
                content = "—"
            else:
                lag = cell["best_lag"]
                rho = cell["best_rho"]
                content = f"{lag:+d}/{rho:+.2f}"
            row_cells.append(content.center(col_w))
        lines.append(f"  {sa:<{label_w}}  │ {''.join(row_cells)}")

    lines.append("")
    if payload["strongest"]:
        s = payload["strongest"]
        lines.append("  Strongest signal:")
        lines.append(f"    {s['direction']}  (ρ={s['best_rho']:+.2f}, "
                     f"strength={s['strength']}, n={s['overlap_days']})")
    elif payload["pairs_computed"] == 0:
        lines.append("  ⚠️  No pair has enough overlapping days yet — "
                     f"wait until ≥{MIN_OVERLAP} days of synth history accumulate.")
    else:
        lines.append("  (no signal stronger than weak threshold)")

    return "\n".join(lines)


def _format_pair(payload: dict) -> str:
    if payload.get("insufficient_data"):
        return (
            f"🔄 Lead/Lag — {payload['source_a']} vs {payload['source_b']}\n\n"
            f"  ⚠️  Insufficient data: {payload['reason']}\n"
            f"     n_samples={payload['n_samples']}, "
            f"overlap_days={payload['overlap_days']}"
        )

    lines = [
        f"🔄 Lead/Lag — {payload['source_a']} vs {payload['source_b']}",
        "",
        f"  best lag : {payload['best_lag']:+d} days",
        f"  best ρ   : {payload['best_rho']:+.3f}  ({payload['strength']})",
        f"  samples  : n={payload['n_samples']}, overlap={payload['overlap_days']}",
        "",
        f"  → {payload['direction']}",
        "",
        "  CCF (lag → ρ):",
    ]
    # Print only every other lag to keep terminal-friendly width.
    keys = sorted(payload["ccf"].keys())
    for k in keys:
        rho = payload["ccf"][k]
        if rho is None:
            bar = "(undef)"
        else:
            # ±10-wide histogram bar around zero
            mag = round(abs(rho) * 10)
            if rho >= 0:
                bar = "·" * 10 + "│" + "█" * mag + " " * (10 - mag)
            else:
                bar = " " * (10 - mag) + "█" * mag + "│" + "·" * 10
        rho_s = f"{rho:+.2f}" if rho is not None else "  ·  "
        marker = " ← peak" if k == payload["best_lag"] else ""
        lines.append(f"    lag {k:+3d}  {rho_s}  {bar}{marker}")
    return "\n".join(lines)


# --- CLI -------------------------------------------------------------------

DEFAULT_SOURCES = [
    "youtube_trending",
    "tiktok_creative",
    "pytrends_sector",
    "naver_datalab",
]


def _send_telegram(text: str) -> bool:
    from auto_project.notify import telegram, escape
    return telegram(f"<pre>{escape(text)}</pre>", parse_mode="HTML")


def main() -> int:
    p = argparse.ArgumentParser(description="Lead/lag (cross-correlation) between sources")
    p.add_argument("--keyword", required=True, help="Keyword to analyse")
    p.add_argument("--from", dest="start", required=True,
                   help="Start date (YYYY-MM-DD, today, lastNd, ...)")
    p.add_argument("--to", dest="end", default=None, help="End date (default today)")
    p.add_argument("--source-a", default=None,
                   help="Single-pair mode: first source")
    p.add_argument("--source-b", default=None,
                   help="Single-pair mode: second source")
    p.add_argument("--max-lag", type=int, default=DEFAULT_MAX_LAG,
                   help=f"Max ±lag to scan (default {DEFAULT_MAX_LAG})")
    p.add_argument("--telegram", action="store_true",
                   help="Send the report to Telegram instead of stdout.")
    args = p.parse_args()

    start, end = parse_range(args.start, args.end)

    single = args.source_a and args.source_b
    if single:
        payload = find_lead(args.keyword, args.source_a, args.source_b,
                            start, end, max_lag=args.max_lag)
        report = _format_pair(payload)
    else:
        payload = pairwise_lag_matrix(args.keyword, DEFAULT_SOURCES,
                                      start, end, max_lag=args.max_lag)
        report = _format_matrix(payload)

    if args.telegram:
        ok = _send_telegram(report)
        print(report)
        print(f"\n[telegram sent={ok}]")
        return 0 if ok else 1

    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
