"""CLI: top-k keywords across a date range.

Example:
    python -m analytics.top --from 2026-04-16 --to 2026-05-03
    python -m analytics.top --from last7d --k 15
    python -m analytics.top --from last30d --min-days 3 --telegram

Answers the "what was hot last week / month?" question — the keyword-less
companion to ``analytics.intensity``. Uses mean ``trend_score`` over the
window so that a one-day spike doesn't beat a steady multi-day climb.
"""
from __future__ import annotations

import argparse
import sys

from analytics import timeline
from analytics._dateparse import parse_range


def _format_report(payload: dict) -> str:
    header = (
        f"🏆 Top keywords  ·  {payload['start']} → {payload['end']}\n"
        f"  scanned {payload['n_days_with_snapshots']}일, "
        f"{payload['n_keywords_seen']} unique keywords seen "
        f"(min_days filter = {payload['min_days_filter']})"
    )
    rows = payload["top"]
    if not rows:
        return f"{header}\n\n  (no snapshots in range)"

    out = [header, ""]
    out.append(f"  {'#':>2}  {'keyword':<28}  {'mean':>6}  {'days':>4}  rate  sources")
    out.append(f"  {'─'*2}  {'─'*28}  {'─'*6}  {'─'*4}  ────  ───────")
    for i, r in enumerate(rows, 1):
        kw = r["keyword"]
        if len(kw) > 28:
            kw = kw[:25] + "..."
        srcs = ",".join(s.replace("_sector", "").replace("_creative", "").replace("_trending", "")
                        for s in r["sources"])
        out.append(
            f"  {i:>2}  {kw:<28}  {r['mean_score']:>6.1f}  "
            f"{r['days_present']:>4}  {r['presence_rate']:.0%}  {srcs}"
        )
    return "\n".join(out)


def _send_telegram(text: str) -> bool:
    from auto_project.notify import telegram, escape
    safe = escape(text)
    return telegram(f"<pre>{safe}</pre>", parse_mode="HTML")


def main() -> int:
    p = argparse.ArgumentParser(description="Top-k keywords across a date range")
    p.add_argument("--from", dest="start", required=True,
                   help="Start date (YYYY-MM-DD, today, yesterday, last7d, ...)")
    p.add_argument("--to", dest="end", default=None,
                   help="End date (defaults to today)")
    p.add_argument("--k", type=int, default=15,
                   help="How many top rows to return. Default 15.")
    p.add_argument("--min-days", type=int, default=1, dest="min_days",
                   help="Require keyword to appear on at least this many days. "
                        "Default 1 (no filter); raise to 3+ on month windows.")
    p.add_argument("--telegram", action="store_true",
                   help="POST the report to Telegram instead of stdout.")
    args = p.parse_args()

    start, end = parse_range(args.start, args.end)
    payload = timeline.top_in_range(start, end, k=args.k, min_days=args.min_days)

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
