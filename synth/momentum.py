"""Time-series momentum on top of synth_hot_keywords snapshots.

Reads the past ~14 days of synth output, aligns by day (latest snapshot per
day wins), and computes per-keyword:

  z_score      — today's deviation from the 7-day baseline, in units of sigma
  velocity     — score_today − score_yesterday (1st derivative)
  acceleration — change in velocity day-over-day (2nd derivative)

A noise floor (MIN_STD) prevents tiny variations on low-volume keywords from
generating false breakouts when sigma collapses near zero.

Output is written to state/momentum/<UTC-iso>.json and consumed by
agents.trend_brief to build the daily marketing brief.

CLI:
    python -m synth.momentum             # write a new snapshot
    python -m synth.momentum --dry-run   # print top breakouts, no write
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import statistics
from collections import defaultdict
from typing import Any

from crawlers._common import state_dir, write_snapshot


# Tunables — exposed as module constants so callers/tests can override.
HISTORY_DAYS = 14          # how far back to read snapshots
BASELINE_WINDOW = 7        # last N days used as the baseline (excluding today)
MIN_STD = 5.0              # noise floor on a 0-100 score; below this, treat as noise
Z_CAP = 10.0               # clamp on |z_score| for JSON safety
Z_BREAKOUT = 2.0           # standard 2-sigma threshold
Z_RISING = 1.0
Z_DECLINING = -1.0
MIN_HISTORY = 3            # below this many days of presence → label "New"


def _snapshot_date(snap: dict[str, Any]) -> _dt.date | None:
    """Extract the UTC calendar date from a synth snapshot."""
    ts = (snap.get("_meta") or {}).get("fetched_at")
    if not ts:
        return None
    # ISO 8601 — strip any trailing 'Z' for fromisoformat in older pythons
    ts = ts.replace("Z", "+00:00")
    try:
        return _dt.datetime.fromisoformat(ts).date()
    except ValueError:
        return None


def _load_history(source: str = "synth_hot_keywords") -> list[tuple[_dt.date, dict[str, Any]]]:
    """Return (date, snapshot) pairs for the most recent HISTORY_DAYS days.

    When a day has multiple snapshots (synth runs ad hoc), the last one wins
    — gives us a stable end-of-day reading consistent with how marketing
    dashboards usually report.
    """
    d = state_dir(source)
    files = sorted(d.glob("*.json"))
    by_day: dict[_dt.date, dict[str, Any]] = {}
    for f in files:
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        day = _snapshot_date(snap)
        if day is None:
            continue
        by_day[day] = snap  # later files overwrite earlier same-day ones
    days = sorted(by_day)[-HISTORY_DAYS:]
    return [(d, by_day[d]) for d in days]


def _score_map(snap: dict[str, Any]) -> dict[str, float]:
    """Extract {keyword: trend_score} from a synth snapshot."""
    out: dict[str, float] = {}
    for kw in snap.get("keywords") or []:
        name = kw.get("keyword")
        if name:
            out[name] = float(kw.get("trend_score") or 0.0)
    return out


def _classify(history_days: int, z: float, acceleration: float) -> str:
    """Map (history, z, acceleration) → one of 5 labels."""
    if history_days < MIN_HISTORY:
        return "🆕 New"
    if z >= Z_BREAKOUT and acceleration > 0:
        return "🔥 Breakout"
    if z >= Z_RISING and acceleration > 0:
        return "📈 Rising"
    if z <= Z_DECLINING:
        return "📉 Declining"
    return "💤 Steady"


def compute(
    history: list[tuple[_dt.date, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Compute momentum from a list of (date, synth_snapshot) entries.

    `history` is in ascending date order; the last entry is "today".
    """
    history = history if history is not None else _load_history()
    if not history:
        return {"as_of": None, "history_days_available": 0, "keywords": []}

    as_of_date, today_snap = history[-1]
    past = history[:-1]

    today_scores = _score_map(today_snap)
    past_scores_by_day: list[dict[str, float]] = [_score_map(s) for _, s in past]

    # Universe of keywords seen at any point in the window.
    universe: set[str] = set(today_scores)
    for m in past_scores_by_day:
        universe.update(m)

    # For each keyword, build the per-day time series for the baseline window.
    baseline_window = past_scores_by_day[-BASELINE_WINDOW:]

    # Also preserve today's source attribution from the synth snapshot.
    today_sources_by_kw: dict[str, list[str]] = {
        kw.get("keyword"): list(kw.get("sources") or [])
        for kw in (today_snap.get("keywords") or [])
        if kw.get("keyword")
    }

    results: list[dict[str, Any]] = []
    for kw in universe:
        score_today = today_scores.get(kw, 0.0)
        past_vals = [day.get(kw, 0.0) for day in baseline_window]
        # `history_days`: distinct calendar days where this keyword was present
        # (any non-zero score), capped by what we actually have.
        present_days = sum(1 for v in past_vals if v > 0)
        if score_today > 0:
            present_days += 1

        if past_vals:
            mean_7d = statistics.fmean(past_vals)
            raw_std = statistics.pstdev(past_vals) if len(past_vals) >= 2 else 0.0
        else:
            mean_7d = score_today
            raw_std = 0.0

        # Regularize std with a floor — protects against false breakouts when a
        # keyword's past values happen to be near-constant noise.
        effective_std = max(raw_std, MIN_STD)
        z_raw = (score_today - mean_7d) / effective_std
        z_score = max(-Z_CAP, min(Z_CAP, z_raw))

        # Velocity & acceleration use the actual recent values (not the window).
        score_yesterday = past_vals[-1] if past_vals else 0.0
        velocity = score_today - score_yesterday
        if len(past_vals) >= 2:
            day_before = past_vals[-2]
            velocity_yesterday = score_yesterday - day_before
            acceleration = velocity - velocity_yesterday
        else:
            acceleration = 0.0

        label = _classify(present_days, z_score, acceleration)

        results.append({
            "keyword": kw,
            "label": label,
            "z_score": round(z_score, 2),
            "velocity": round(velocity, 2),
            "acceleration": round(acceleration, 2),
            "score_today": round(score_today, 2),
            "mean_7d": round(mean_7d, 2),
            "std_7d": round(raw_std, 2),
            "history_days": present_days,
            "sources": today_sources_by_kw.get(kw, []),
        })

    # Stable sort: breakouts first, then by z desc, then score desc.
    label_order = {"🔥 Breakout": 0, "📈 Rising": 1, "💤 Steady": 2, "📉 Declining": 3, "🆕 New": 4}
    results.sort(key=lambda r: (
        label_order.get(r["label"], 99),
        -r["z_score"],
        -r["score_today"],
    ))

    return {
        "as_of": as_of_date.isoformat(),
        "history_days_available": len(history),
        "baseline_window": BASELINE_WINDOW,
        "thresholds": {
            "z_breakout": Z_BREAKOUT,
            "z_rising": Z_RISING,
            "z_declining": Z_DECLINING,
            "min_std": MIN_STD,
            "min_history": MIN_HISTORY,
        },
        "label_counts": _count_labels(results),
        "keywords": results,
    }


def _count_labels(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in results:
        counts[r["label"]] += 1
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute keyword momentum")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print summary, don't write a snapshot.")
    args = parser.parse_args()

    payload = compute()

    if not args.dry_run:
        target = write_snapshot("momentum", payload)
        print(f"momentum → {target}")
    print(f"as_of={payload['as_of']}  history_days_available={payload['history_days_available']}")
    print(f"labels: {payload['label_counts']}")
    # Show top 10 non-New, non-Steady items
    interesting = [r for r in payload["keywords"]
                   if r["label"] in ("🔥 Breakout", "📈 Rising", "📉 Declining")][:10]
    for r in interesting:
        print(f"  {r['label']:<14} z={r['z_score']:>+5.2f} "
              f"vel={r['velocity']:>+6.1f} acc={r['acceleration']:>+6.1f} "
              f"{r['keyword']}")


if __name__ == "__main__":
    main()
