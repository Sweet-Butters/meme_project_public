"""Naver DataLab (search trends) crawler — official open API.

Endpoint: POST https://openapi.naver.com/v1/datalab/search

Headers:
  X-Naver-Client-Id     ← NAVER_CLIENT_ID
  X-Naver-Client-Secret ← NAVER_CLIENT_SECRET

Returns relative search ratios (max value normalized to 100) for the
requested keyword groups over the given date range. Unlike pytrends this
is OFFICIAL and stable, with a generous free quota (~25,000 calls/day for
DataLab specifically).

Note: DataLab gives **relative** ratios, not absolute volumes. For real
absolute monthly search volumes, use crawlers.naver_search_ad.

Run directly:
    python -m crawlers.naver_datalab --keywords AI 딥러닝 --start 2026-04-01
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from typing import Any

import requests

from ._common import require_env, write_snapshot

API_URL = "https://openapi.naver.com/v1/datalab/search"


def _fetch_datalab(
    client_id: str,
    client_secret: str,
    keyword_groups: list[dict[str, Any]],
    start_date: str,
    end_date: str,
    time_unit: str = "date",
    device: str | None = None,
    gender: str | None = None,
    ages: list[str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """One API call. `keyword_groups` is the DataLab JSON shape: a list of
    {"groupName": "...", "keywords": ["...", ...]} (max 5 groups, 20 kw each)."""
    body: dict[str, Any] = {
        "startDate": start_date,
        "endDate": end_date,
        "timeUnit": time_unit,  # date | week | month
        "keywordGroups": keyword_groups,
    }
    if device:
        body["device"] = device  # pc | mo
    if gender:
        body["gender"] = gender  # m | f
    if ages:
        body["ages"] = ages       # ["1", "2", ...] mapping to age buckets

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
        "Content-Type": "application/json",
    }
    resp = requests.post(API_URL, headers=headers, data=json.dumps(body), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _default_date_range(days: int = 30) -> tuple[str, str]:
    today = _dt.date.today()
    start = today - _dt.timedelta(days=days)
    return start.isoformat(), today.isoformat()


# DataLab caps a single request at 5 keyword groups.
DATALAB_MAX_GROUPS = 5


def _build_keyword_groups(
    keywords: list[str], single_group: bool = False
) -> list[dict[str, Any]]:
    """Turn a flat keyword list into DataLab ``keywordGroups``.

    Default (``single_group=False``): **one group per keyword**. DataLab then
    returns a separate time series per keyword, all on a shared 0-100 scale
    (max across every group/period = 100), so the keywords are actually
    comparable. This is what the synth layer needs — it can rank AI vs 딥러닝
    vs ChatGPT against each other.

    Legacy (``single_group=True``): lump every keyword into one group. DataLab
    then returns ONE combined series shared by all members, so downstream every
    member keyword gets the *identical* mean ratio — a zero-discrimination
    "dead signal" that inflates scores without separating keywords. Avoid for
    ranking; kept only for callers that genuinely want a group aggregate.
    """
    kws = [k.strip() for k in (keywords or []) if k and k.strip()]
    if not kws:
        raise ValueError("naver_datalab: no keywords given")
    if single_group:
        return [{"groupName": "g1", "keywords": kws}]
    if len(kws) > DATALAB_MAX_GROUPS:
        raise ValueError(
            f"per-keyword mode supports up to {DATALAB_MAX_GROUPS} keywords "
            f"per snapshot (DataLab caps keywordGroups at 5); got {len(kws)}. "
            "Split into multiple runs, or pass single_group=True."
        )
    return [{"groupName": kw, "keywords": [kw]} for kw in kws]


def crawl(
    keyword_groups: list[dict[str, Any]],
    start_date: str | None = None,
    end_date: str | None = None,
    time_unit: str = "date",
    breakdown_by: list[str] | None = None,
) -> dict[str, Any]:
    """One snapshot with the requested groups + optional demographic breakdowns.

    breakdown_by accepts items like "device:pc", "device:mo", "gender:m",
    "gender:f". Each runs one additional API call. Keep this list short —
    each call still counts against quota.
    """
    env = require_env("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")
    cid, csec = env["NAVER_CLIENT_ID"], env["NAVER_CLIENT_SECRET"]

    if not start_date or not end_date:
        start_date, end_date = _default_date_range(30)

    main = _fetch_datalab(cid, csec, keyword_groups, start_date, end_date, time_unit)

    breakdowns: dict[str, Any] = {}
    for spec in breakdown_by or []:
        if ":" not in spec:
            continue
        key, val = spec.split(":", 1)
        kwargs: dict[str, Any] = {}
        if key == "device":
            kwargs["device"] = val
        elif key == "gender":
            kwargs["gender"] = val
        elif key == "ages":
            kwargs["ages"] = [v.strip() for v in val.split(",")]
        else:
            continue
        breakdowns[spec] = _fetch_datalab(
            cid, csec, keyword_groups, start_date, end_date, time_unit, **kwargs
        )

    payload = {
        "keyword_groups": keyword_groups,
        "start_date": start_date,
        "end_date": end_date,
        "time_unit": time_unit,
        "main": main,
        "breakdowns": breakdowns,
    }
    target = write_snapshot("naver_datalab", payload)
    payload["_written_to"] = str(target)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Naver DataLab crawler")
    parser.add_argument("--keywords", nargs="+", required=True,
                        help="Keywords to track. By default each becomes its own "
                             "DataLab group so they're individually comparable.")
    parser.add_argument("--single-group", action="store_true",
                        help="Lump all keywords into one group (legacy aggregate; "
                             "no per-keyword discrimination — avoid for ranking).")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD (default: 30 days ago)")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--time-unit", choices=["date", "week", "month"], default="date")
    parser.add_argument("--breakdown", nargs="*", default=None,
                        help="e.g. device:pc device:mo gender:f")
    args = parser.parse_args()

    groups = _build_keyword_groups(args.keywords, single_group=args.single_group)
    result = crawl(groups, args.start, args.end, args.time_unit, args.breakdown)
    mode = "single-group" if args.single_group else f"{len(groups)} per-keyword groups"
    print(f"DataLab {args.start or 'auto'}..{args.end or 'today'} "
          f"({mode}) → {result['_written_to']}")


if __name__ == "__main__":
    main()
