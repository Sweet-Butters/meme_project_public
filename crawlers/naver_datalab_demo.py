"""Per-keyword demographic crawler over Naver DataLab.

Builds on `naver_datalab._fetch_datalab` but issues a structured matrix
of requests so each keyword gets its own gender / age / device time
series. The existing crawler is single-group-multi-keyword (group ratio
is shared across all keywords in a group); this one is single-keyword
single-group so the demographic breakdown reflects that specific keyword.

Per keyword, we request:
  - baseline       (no filter)
  - gender_m       (gender="m")
  - gender_f       (gender="f")
  - ages_<bucket>  (ages=[...])  × 6 buckets
  - device_pc, device_mo

→ 11 calls per keyword. 3 default keywords × 11 = 33 calls per snapshot.
DataLab free quota is ~25,000/day so even hourly cron is fine.

Output:
    state/naver_datalab_demo/<UTC-iso>.json
    {
      "_meta": {...},
      "start_date": "...", "end_date": "...", "time_unit": "...",
      "keywords": {
        "AI": {
          "baseline":   [{"period": "...", "ratio": ...}, ...],
          "gender":     {"m": [...], "f": [...]},
          "ages":       {"teens": [...], "20s": [...], "30s": [...], ...},
          "device":     {"pc": [...], "mo": [...]}
        }, ...
      }
    }

Run directly:
    python -m crawlers.naver_datalab_demo --keywords AI 딥러닝 ChatGPT
    python -m crawlers.naver_datalab_demo --keywords AI --start 2026-04-15 --end 2026-05-21
"""
from __future__ import annotations

import argparse
import datetime as _dt
from typing import Any

from ._common import require_env, write_snapshot
from .naver_datalab import _fetch_datalab, _default_date_range


# NAVER DataLab age bands → marketing-friendly buckets.
AGE_BUCKETS: dict[str, list[str]] = {
    "teens": ["1", "2"],   # under 13 + 13-18
    "20s":   ["3", "4"],   # 19-24 + 25-29
    "30s":   ["5", "6"],   # 30-34 + 35-39
    "40s":   ["7", "8"],
    "50s":   ["9", "10"],
    "60+":   ["11"],
}


def _extract_series(api_response: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the single result's data array out of a DataLab response.

    DataLab returns `{results: [{title, keywords, data: [...]}]}`. We
    always issue one-group queries here, so we just take results[0].data
    or an empty list if anything's missing.
    """
    results = api_response.get("results") or []
    if not results:
        return []
    return results[0].get("data") or []


def crawl_keyword_demographics(
    keyword: str,
    start_date: str,
    end_date: str,
    time_unit: str = "date",
) -> dict[str, Any]:
    """Issue the full 11-call matrix for a single keyword."""
    env = require_env("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")
    cid, csec = env["NAVER_CLIENT_ID"], env["NAVER_CLIENT_SECRET"]

    groups = [{"groupName": "k", "keywords": [keyword]}]

    def _call(**kwargs: Any) -> list[dict[str, Any]]:
        try:
            resp = _fetch_datalab(
                cid, csec, groups, start_date, end_date, time_unit, **kwargs,
            )
        except Exception as e:
            print(f"  {keyword}: call failed ({kwargs}): {e!r}")
            return []
        return _extract_series(resp)

    return {
        "baseline": _call(),
        "gender": {
            "m": _call(gender="m"),
            "f": _call(gender="f"),
        },
        "ages": {label: _call(ages=bands) for label, bands in AGE_BUCKETS.items()},
        "device": {
            "pc": _call(device="pc"),
            "mo": _call(device="mo"),
        },
    }


def crawl(
    keywords: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    time_unit: str = "date",
) -> dict[str, Any]:
    """Snapshot all `keywords` with full demographic breakdown."""
    if not start_date or not end_date:
        start_date, end_date = _default_date_range(30)

    per_keyword: dict[str, dict[str, Any]] = {}
    for kw in keywords:
        print(f"fetching demographics for {kw} ({start_date} → {end_date})...")
        per_keyword[kw] = crawl_keyword_demographics(
            kw, start_date, end_date, time_unit,
        )

    payload = {
        "start_date": start_date,
        "end_date": end_date,
        "time_unit": time_unit,
        "age_buckets": AGE_BUCKETS,
        "keywords": per_keyword,
    }
    target = write_snapshot("naver_datalab_demo", payload)
    payload["_written_to"] = str(target)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Naver DataLab per-keyword demographic crawler",
    )
    parser.add_argument("--keywords", nargs="+", required=True,
                        help="Keywords to crawl (each gets its own group).")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD (default: 30 days ago)")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--time-unit", choices=["date", "week", "month"], default="date")
    args = parser.parse_args()

    result = crawl(args.keywords, args.start, args.end, args.time_unit)
    print(f"\nDataLab demo → {result['_written_to']}")
    for kw in args.keywords:
        rows = (result["keywords"].get(kw) or {}).get("baseline") or []
        print(f"  {kw}: {len(rows)} baseline data points")


if __name__ == "__main__":
    main()
