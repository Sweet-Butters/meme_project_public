"""Timeline queries over the crawler/synth state snapshots.

Read-only against ``state/<source>/*.json``. Three primitives:

  list_snapshots(start, end, source)
      All ``(timestamp, snapshot)`` pairs in the inclusive date range.

  keyword_intensity(keyword, start, end, source=...)
      Per-day time series of a single keyword's value, plus summary stats
      ready for sparkline / bar-chart rendering.

  top_in_range(start, end, k=20)
      Top-k keywords by mean ``trend_score`` over the window — the
      "what was hot last week?" question, without naming the keyword.

Aggregation policy: when multiple snapshots fall on the same UTC calendar
day, the latest one wins. This matches the daily-marketing-dashboard
convention used in synth.momentum so reports are mutually consistent.

Filenames are expected to start with an ISO-8601 timestamp like
``2026-05-22T00-31-10+00-00.json`` (colons replaced with dashes per the
``crawlers._common.write_snapshot`` convention).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from collections import defaultdict
from typing import Any

from crawlers._common import state_dir


# Match the leading "YYYY-MM-DDTHH-MM-SS+ZZ-ZZ" / "Z" stem produced by
# write_snapshot. We rely on the *date* portion, so be lenient with the rest.
_TS_PREFIX = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")


def _parse_filename_ts(name: str) -> _dt.datetime | None:
    """Extract the UTC timestamp from a snapshot filename, or None."""
    m = _TS_PREFIX.match(name)
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x) for x in m.groups())
    try:
        return _dt.datetime(y, mo, d, h, mi, s, tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def list_snapshots(
    start: _dt.date,
    end: _dt.date,
    source: str,
) -> list[tuple[_dt.datetime, dict[str, Any]]]:
    """Return all snapshots whose UTC date falls in [start, end] (inclusive)."""
    d = state_dir(source)
    out: list[tuple[_dt.datetime, dict[str, Any]]] = []
    for f in sorted(d.glob("*.json")):
        ts = _parse_filename_ts(f.name)
        if ts is None:
            continue
        day = ts.date()
        if day < start or day > end:
            continue
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append((ts, snap))
    return out


def _daily_aggregate(
    snapshots: list[tuple[_dt.datetime, dict[str, Any]]],
) -> dict[_dt.date, dict[str, Any]]:
    """Pick the latest snapshot per UTC calendar day (matches momentum policy)."""
    by_day: dict[_dt.date, tuple[_dt.datetime, dict[str, Any]]] = {}
    for ts, snap in snapshots:
        day = ts.date()
        prior = by_day.get(day)
        if prior is None or ts > prior[0]:
            by_day[day] = (ts, snap)
    return {day: snap for day, (_, snap) in by_day.items()}


def _value_in_synth(snap: dict[str, Any], keyword: str,
                    source_field: str | None) -> float:
    """Pull a keyword's value out of a synth_hot_keywords snapshot.

    `source_field`:
      None  → use the top-level ``trend_score`` (cross-source unified).
      otherwise → use ``raw[source_field]`` (the per-source signal). If the
                  keyword isn't in that source on this day, return 0.
    """
    for kw in snap.get("keywords") or []:
        if kw.get("keyword") == keyword:
            if source_field is None:
                return float(kw.get("trend_score") or 0.0)
            raw = kw.get("raw") or {}
            return float(raw.get(source_field) or 0.0)
    return 0.0


def keyword_intensity(
    keyword: str,
    start: _dt.date,
    end: _dt.date,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """Per-day series of `keyword`'s intensity over the given window.

    Always reads from ``synth_hot_keywords`` (which carries both the unified
    ``trend_score`` *and* per-source ``raw`` values). When `source` is None
    we report ``trend_score``; when it names a crawler (e.g. ``naver_datalab``)
    we report that source's ``raw`` value within synth.

    The returned series is dense — every day in [start, end] gets an entry,
    with 0.0 for days where the keyword wasn't present in that day's synth.
    A dense series makes the sparkline/bar visualisation actually readable.
    """
    snapshots = list_snapshots(start, end, "synth_hot_keywords")
    by_day = _daily_aggregate(snapshots)

    days: list[_dt.date] = []
    values: list[float] = []
    cur = start
    while cur <= end:
        days.append(cur)
        snap = by_day.get(cur)
        values.append(_value_in_synth(snap, keyword, source) if snap else 0.0)
        cur += _dt.timedelta(days=1)

    return {
        "keyword": keyword,
        "source": source or "synth_hot_keywords.trend_score",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": [d.isoformat() for d in days],
        "values": values,
        "snapshots_in_range": len(snapshots),
        "days_with_data": sum(1 for v in values if v > 0),
    }


def top_in_range(
    start: _dt.date,
    end: _dt.date,
    *,
    k: int = 20,
    min_days: int = 1,
) -> dict[str, Any]:
    """Top-k keywords by mean ``trend_score`` across the daily-aggregated window.

    `min_days` filters out flukes: a keyword must appear in at least this many
    days within the window to be considered. Default 1 (no filter) is fine
    for short windows; bump it for monthly views.
    """
    snapshots = list_snapshots(start, end, "synth_hot_keywords")
    by_day = _daily_aggregate(snapshots)

    sum_score: dict[str, float] = defaultdict(float)
    days_present: dict[str, int] = defaultdict(int)
    sources: dict[str, set[str]] = defaultdict(set)

    for snap in by_day.values():
        for kw in snap.get("keywords") or []:
            name = kw.get("keyword")
            if not name:
                continue
            sum_score[name] += float(kw.get("trend_score") or 0.0)
            days_present[name] += 1
            for s in kw.get("sources") or []:
                sources[name].add(s)

    n_days = len(by_day)
    rows: list[dict[str, Any]] = []
    for name, total in sum_score.items():
        present = days_present[name]
        if present < min_days:
            continue
        rows.append({
            "keyword": name,
            "mean_score": round(total / present, 2),
            "days_present": present,
            "presence_rate": round(present / n_days, 2) if n_days else 0.0,
            "sources": sorted(sources[name]),
        })
    rows.sort(key=lambda r: (-r["mean_score"], -r["days_present"]))
    rows = rows[:k]

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "n_days_with_snapshots": n_days,
        "n_keywords_seen": len(sum_score),
        "min_days_filter": min_days,
        "top": rows,
    }
