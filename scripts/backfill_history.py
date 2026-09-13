"""One-shot historical backfill for synth_hot_keywords.

Reconstructs per-day synth snapshots over a past date range by re-querying
the two API sources that *can* return history (pytrends + Naver DataLab).
YouTube/TikTok are explicitly skipped: their "trending" endpoints only
expose the present, so there's nothing to recover from.

Safety
------
- For each day in range, checks whether a synth snapshot with real
  (non-empty) keywords already exists. If yes, that day is SKIPPED — we
  never overwrite production data.
- Backfilled files are written at ``T23-59-59`` so the daily-aggregation
  policy in synth.momentum picks them up automatically: this timestamp
  beats any production cron (crawl_trending's last run is at 22:30 UTC).
  Re-running the script is idempotent — old backfills for the same day
  are deleted before the new one is written.
- Each snapshot carries ``_meta.backfilled = True`` so callers can
  distinguish replayed from live data.

Usage
-----
    python -m scripts.backfill_history --from 2026-04-15 --to 2026-05-21
    python -m scripts.backfill_history --from 2026-04-15 --to 2026-05-21 --dry-run

Requires the same Naver creds as crawl_sectors:
    NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from typing import Any

# We re-use analytics' tolerant date parser for consistency with the CLIs.
from analytics._dateparse import parse_date
from crawlers._common import state_dir


# Match crawl_sectors.yml's keyword list so backfilled days are comparable
# with going-forward production data.
KEYWORDS = ["AI", "딥러닝", "ChatGPT"]


# --- source fetchers -------------------------------------------------------

def _build_proxies() -> list[str] | None:
    """Build a proxy URL list from environment.

    Priority:
      1. WEBSHARE_USER + WEBSHARE_PASS → WebShare proxies. Hosts come
         from WEBSHARE_HOSTS (comma-separated `IP:PORT,IP:PORT,...`)
         when set, else fall back to single WEBSHARE_HOST, else the paid
         backconnect endpoint `p.webshare.io:80`.
         The pytrends crawler is given the full list — pytrends rotates
         through them on retry, multiplying the effective free-tier
         capacity and dodging single-IP rate-limits.
      2. HTTPS_PROXY (single proxy URL).
      3. None → direct connection.
    """
    user = os.environ.get("WEBSHARE_USER")
    pwd = os.environ.get("WEBSHARE_PASS")
    if user and pwd:
        hosts_csv = os.environ.get("WEBSHARE_HOSTS")
        if hosts_csv:
            hosts = [h.strip() for h in hosts_csv.split(",") if h.strip()]
        else:
            hosts = [os.environ.get("WEBSHARE_HOST", "p.webshare.io:80")]
        return [f"http://{user}:{pwd}@{h}" for h in hosts]
    https = os.environ.get("HTTPS_PROXY")
    if https:
        return [https]
    return None


def _iter_chunks(start: _dt.date, end: _dt.date, days: int = 7):
    """Yield (chunk_start, chunk_end) windows of at most `days` days each.

    pytrends rate-limits long-range queries much harder than short ones —
    Google charges more per-request for queries that fan out across many
    timeframes server-side. 7-day chunks are the documented sweet spot.
    """
    cur = start
    one_day = _dt.timedelta(days=1)
    chunk_span = _dt.timedelta(days=days - 1)
    while cur <= end:
        chunk_end = min(cur + chunk_span, end)
        yield cur, chunk_end
        cur = chunk_end + one_day


def _df_to_by_day(df) -> dict[_dt.date, dict[str, float]]:
    by_day: dict[_dt.date, dict[str, float]] = {}
    for ts, row in df.iterrows():
        day = ts.date()
        by_day[day] = {kw: float(row.get(kw, 0) or 0) for kw in KEYWORDS}
    return by_day


def _fetch_pytrends_chunk(
    start: _dt.date, end: _dt.date, proxies: list[str] | None,
) -> dict[_dt.date, dict[str, float]]:
    """One range × up to 3 retries. Empty dict if every attempt fails."""
    from pytrends.request import TrendReq
    timeframe = f"{start.isoformat()} {end.isoformat()}"
    for attempt in range(3):
        try:
            client = TrendReq(hl="ko-KR", tz=540,
                              proxies=proxies if proxies else None)
            client.build_payload(KEYWORDS, geo="KR", timeframe=timeframe)
            df = client.interest_over_time()
            if df is not None and not df.empty:
                return _df_to_by_day(df)
            # An empty df without exception = rate-limited / blocked.
            print(f"      attempt {attempt+1}: empty response (likely blocked)",
                  file=sys.stderr)
        except Exception as e:
            print(f"      attempt {attempt+1}: {type(e).__name__}: {e}",
                  file=sys.stderr)
        if attempt < 2:
            time.sleep(15 * (attempt + 1))   # 15s, 30s
    return {}


def fetch_pytrends(start: _dt.date, end: _dt.date) -> dict[_dt.date, dict[str, float]]:
    """Daily Google Trends interest_over_time for the 3 seed keywords.

    Strategy:
      - chunk the range into 7-day windows (Google rate-limits long ranges
        harder than short ones)
      - per chunk: try up to 3 times with short backoff
      - optionally route through a residential proxy (WebShare) when env
        vars are present — same proxy Notes_project uses for transcripts
    """
    try:
        from pytrends.request import TrendReq  # noqa: F401
    except ImportError:
        print("  pytrends: package not installed — skipping", file=sys.stderr)
        return {}

    proxies = _build_proxies()
    chunks = list(_iter_chunks(start, end, days=7))
    print(f"  pytrends: {len(chunks)} chunks × ~7 days, "
          f"proxy={'WebShare/HTTPS_PROXY' if proxies else 'direct'}")

    by_day: dict[_dt.date, dict[str, float]] = {}
    succeeded = 0
    for i, (a, b) in enumerate(chunks):
        chunk = _fetch_pytrends_chunk(a, b, proxies)
        if chunk:
            by_day.update(chunk)
            succeeded += 1
            print(f"    [{i+1}/{len(chunks)}] {a}→{b}: ok ({len(chunk)} days)")
        else:
            print(f"    [{i+1}/{len(chunks)}] {a}→{b}: FAILED")
        if i < len(chunks) - 1:
            time.sleep(5)  # gentle pacing between chunks

    print(f"  pytrends: {succeeded}/{len(chunks)} chunks succeeded, "
          f"{len(by_day)} total days")
    return by_day


def fetch_datalab(start: _dt.date, end: _dt.date) -> dict[_dt.date, dict[str, float]]:
    """Daily Naver DataLab search ratios for the 3 seed keywords.

    DataLab returns a *group* ratio per timestamp (combined volume for any
    keyword in the group). We spread it across each keyword equally —
    same convention as synth.hot_keywords does post-bugfix.
    """
    import requests
    try:
        client_id = os.environ["NAVER_CLIENT_ID"]
        client_secret = os.environ["NAVER_CLIENT_SECRET"]
    except KeyError as e:
        print(f"  datalab: missing env {e.args[0]} — skipping", file=sys.stderr)
        return {}

    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "timeUnit": "date",
        "keywordGroups": [{"groupName": "g1", "keywords": KEYWORDS}],
    }
    try:
        r = requests.post(
            "https://openapi.naver.com/v1/datalab/search",
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  datalab: request failed ({type(e).__name__}: {e})", file=sys.stderr)
        return {}

    by_day: dict[_dt.date, dict[str, float]] = {}
    results = data.get("results") or []
    if not results:
        return {}
    for entry in results[0].get("data") or []:
        try:
            day = _dt.date.fromisoformat(entry["period"])
            ratio = float(entry["ratio"])
        except (KeyError, ValueError, TypeError):
            continue
        by_day[day] = {kw: ratio for kw in KEYWORDS}
    return by_day


# --- synth assembly --------------------------------------------------------

# Match synth.hot_keywords defaults so backfilled trend_scores are
# directly comparable to live ones. If those drift, bump the file.
WEIGHTS = {"pytrends_sector": 0.5, "naver_datalab": 0.5}
CROSS_SOURCE_BONUS = 0.15


def _minmax(d: dict[str, float]) -> dict[str, float]:
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi == lo:
        return {k: 1.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def build_synth_snapshot(
    day: _dt.date,
    pytrends_vals: dict[str, float],
    datalab_vals: dict[str, float],
) -> dict[str, Any]:
    """Construct a synth_hot_keywords-shaped snapshot for `day`.

    Mirrors the logic in synth.hot_keywords.synthesize so the field
    structure and scoring stay byte-compatible with live snapshots.
    """
    per_source_raw: dict[str, dict[str, float]] = {}
    if pytrends_vals:
        per_source_raw["pytrends_sector"] = pytrends_vals
    if datalab_vals:
        per_source_raw["naver_datalab"] = datalab_vals

    per_source_norm = {src: _minmax(sig) for src, sig in per_source_raw.items()}

    combined: dict[str, dict[str, Any]] = {}
    for src, norm in per_source_norm.items():
        w = WEIGHTS.get(src, 0.0)
        raw = per_source_raw[src]
        for kw, n in norm.items():
            row = combined.setdefault(kw, {
                "keyword": kw, "sources": [], "score": 0.0, "raw": {},
            })
            row["sources"].append(src)
            row["score"] += w * n
            row["raw"][src] = raw[kw]

    for row in combined.values():
        extra = max(0, len(row["sources"]) - 1)
        row["score"] *= (1.0 + CROSS_SOURCE_BONUS * extra)

    rows = sorted(combined.values(), key=lambda r: r["score"], reverse=True)
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

    fetched_at = _dt.datetime(day.year, day.month, day.day, 12, 0, 0,
                              tzinfo=_dt.timezone.utc).isoformat()
    return {
        "_meta": {
            "source": "synth_hot_keywords",
            "fetched_at": fetched_at,
            "backfilled": True,
        },
        "sources_used": list(per_source_raw.keys()),
        "weights": WEIGHTS,
        "total_keywords": len(rows),
        "keywords": rows,
    }


# --- write path with production-safety check -------------------------------

def _has_real_data(day: _dt.date) -> bool:
    """True if the day already has a synth snapshot with non-empty keywords."""
    d = state_dir("synth_hot_keywords")
    candidates = sorted(d.glob(f"{day.isoformat()}*.json"))
    if not candidates:
        return False
    for path in reversed(candidates):
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (snap.get("keywords") or []) and not (snap.get("_meta") or {}).get("backfilled"):
            return True
    return False


def _cleanup_previous_backfills(day: _dt.date) -> int:
    """Delete any existing backfill files for this day. Returns count removed."""
    d = state_dir("synth_hot_keywords")
    removed = 0
    for path in d.glob(f"{day.isoformat()}*.json"):
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (snap.get("_meta") or {}).get("backfilled") is True:
            path.unlink()
            removed += 1
    return removed


def write_backfill(day: _dt.date, payload: dict[str, Any]) -> str:
    """Write the backfill snapshot. T23-59-59 guarantees it beats any
    production cron timestamp in the daily-aggregation tiebreaker."""
    _cleanup_previous_backfills(day)
    name = f"{day.isoformat()}T23-59-59+00-00.json"
    path = state_dir("synth_hot_keywords") / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(path)


# --- CLI -------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="start", required=True,
                   help="Start date (YYYY-MM-DD, today, yesterday, lastNd)")
    p.add_argument("--to", dest="end", required=True,
                   help="End date (YYYY-MM-DD, today, yesterday, lastNd)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would happen, don't write files.")
    p.add_argument("--include-demo", action="store_true",
                   help="Also fetch demographic breakdown (gender/age/device) "
                        "by calling crawlers.naver_datalab_demo for the whole range. "
                        "Writes one state/naver_datalab_demo/<ts>.json snapshot, "
                        "not per-day — demographic stats only need one snapshot.")
    args = p.parse_args()

    start = parse_date(args.start)
    end = parse_date(args.end)
    if end < start:
        start, end = end, start
    print(f"backfill range: {start} → {end}  ({(end - start).days + 1} days)")
    print(f"keywords: {KEYWORDS}")
    t0 = time.time()

    if args.include_demo:
        print("fetching demographics ...")
        if args.dry_run:
            print(f"  (dry-run) would call naver_datalab_demo "
                  f"with {start.isoformat()}..{end.isoformat()}")
        else:
            from crawlers import naver_datalab_demo  # late import — keeps top-level light
            demo_result = naver_datalab_demo.crawl(
                KEYWORDS,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
            )
            print(f"  demographics → {demo_result.get('_written_to')}")

    print("fetching pytrends ...")
    pytrends_by_day = fetch_pytrends(start, end)
    print(f"  got {len(pytrends_by_day)} days of pytrends data")

    print("fetching datalab ...")
    datalab_by_day = fetch_datalab(start, end)
    print(f"  got {len(datalab_by_day)} days of datalab data")

    if not pytrends_by_day and not datalab_by_day:
        print("nothing to backfill — both sources returned empty.", file=sys.stderr)
        return 1

    all_days = sorted(set(pytrends_by_day) | set(datalab_by_day))
    print(f"\nprocessing {len(all_days)} day(s) ...")
    written = 0
    skipped = 0
    for day in all_days:
        if _has_real_data(day):
            print(f"  {day}: SKIP (production data already present)")
            skipped += 1
            continue
        snap = build_synth_snapshot(
            day,
            pytrends_by_day.get(day, {}),
            datalab_by_day.get(day, {}),
        )
        n = snap["total_keywords"]
        srcs = ",".join(snap["sources_used"])
        if args.dry_run:
            print(f"  {day}: would write {n} keywords from [{srcs}]")
        else:
            path = write_backfill(day, snap)
            print(f"  {day}: wrote {n} keywords from [{srcs}]  → {path}")
            written += 1

    dt = time.time() - t0
    print(f"\ndone in {dt:.1f}s — wrote {written}, skipped {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
