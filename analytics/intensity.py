"""CLI: per-keyword intensity over a date range.

Example:
    python -m analytics.intensity AI --from 2026-04-16 --to 2026-05-03
    python -m analytics.intensity AI --from last7d
    python -m analytics.intensity AI --from 2026-04-16 --source naver_datalab
    python -m analytics.intensity AI --from last30d --telegram

Output is a single text report: header → bar chart → sparkline → stats.
With ``--telegram``, the same text is wrapped in ``<pre>`` and POSTed to
the user's chat through ``auto_project.notify.telegram``.
"""
from __future__ import annotations

import argparse
import sys

from analytics import _render, timeline
from analytics._dateparse import parse_range


def _format_report(payload: dict) -> str:
    """Compose the human-readable intensity report from a timeline payload."""
    import datetime as _dt
    days = [_dt.date.fromisoformat(d) for d in payload["days"]]
    values = payload["values"]
    n_days = len(days)
    spark = _render.sparkline(values)

    header = (
        f"📈 {payload['keyword']}\n"
        f"  range : {payload['start']} → {payload['end']}  ({n_days}일)\n"
        f"  source: {payload['source']}\n"
        f"  data  : {payload['days_with_data']}/{n_days}일 present, "
        f"{payload['snapshots_in_range']} snapshot(s) in range"
    )

    body = _render.bar_chart(list(zip(days, values)))
    stats = _render.stats_block(values, days)

    return f"{header}\n\n{body}\n\n  sparkline  {spark}\n\n{stats}"


def _send_telegram(text: str) -> bool:
    """Wrap as ``<pre>`` (monospace) and ship through auto_project.notify."""
    from auto_project.notify import telegram, escape
    safe = escape(text)
    return telegram(f"<pre>{safe}</pre>", parse_mode="HTML")


def main() -> int:
    p = argparse.ArgumentParser(description="Keyword intensity over a date range")
    p.add_argument("keyword", help="Keyword to chart (exact match)")
    p.add_argument("--from", dest="start", required=True,
                   help="Start date (YYYY-MM-DD, today, yesterday, last7d, ...)")
    p.add_argument("--to", dest="end", default=None,
                   help="End date (defaults to today)")
    p.add_argument("--source", default=None,
                   help="Restrict to one crawler's raw signal "
                        "(e.g. naver_datalab, pytrends_sector). "
                        "Default: synth_hot_keywords.trend_score")
    p.add_argument("--telegram", action="store_true",
                   help="POST the report to Telegram instead of stdout.")
    args = p.parse_args()

    start, end = parse_range(args.start, args.end)
    payload = timeline.keyword_intensity(args.keyword, start, end, source=args.source)

    report = _format_report(payload)

    if args.telegram:
        ok = _send_telegram(report)
        print(report)
        print(f"\n[telegram sent={ok}]")
        return 0 if ok else 1

    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
