"""Naver Search Ad API — real KR monthly search volumes (absolute numbers).

The one free way to get true Korean search volume. Returns PC + mobile
monthly query counts for up to 5 seed keywords per call, plus a set of
related keywords with their volumes.

Endpoint: GET https://api.naver.com/keywordstool

Auth: every request is signed with HMAC-SHA256:

    timestamp = current epoch milliseconds
    method    = "GET"
    uri       = "/keywordstool"
    sig_input = f"{timestamp}.{method}.{uri}"
    signature = base64( HMAC_SHA256(secret, sig_input) )

Required headers:
    X-Timestamp     ← timestamp
    X-API-KEY       ← NAVER_SEARCH_AD_API_KEY
    X-Customer      ← NAVER_SEARCH_AD_CUSTOMER_ID
    X-Signature     ← signature

Sign-up: searchad.naver.com → 도구 → API 사용 관리 → 발급. Free.

Run directly:
    python -m crawlers.naver_search_ad --keywords AI 딥러닝 머신러닝
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import time
from typing import Any

import requests

from ._common import require_env, write_snapshot

BASE_URL = "https://api.naver.com"
URI = "/keywordstool"


def _sign(secret: str, timestamp_ms: str, method: str, uri: str) -> str:
    msg = f"{timestamp_ms}.{method}.{uri}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _build_headers(api_key: str, secret: str, customer_id: str, method: str = "GET", uri: str = URI) -> dict[str, str]:
    ts = str(int(time.time() * 1000))
    return {
        "X-Timestamp": ts,
        "X-API-KEY": api_key,
        "X-Customer": customer_id,
        "X-Signature": _sign(secret, ts, method, uri),
    }


def _fetch_keywords(
    api_key: str,
    secret: str,
    customer_id: str,
    keywords: list[str],
    show_detail: bool = True,
    timeout: int = 30,
) -> dict[str, Any]:
    """One call. The API takes a comma-joined string of seed keywords.
    Returns the JSON dict including `keywordList` with PC/mobile counts."""
    headers = _build_headers(api_key, secret, customer_id)
    params = {
        "hintKeywords": ",".join(keywords),
        "showDetail": "1" if show_detail else "0",
    }
    resp = requests.get(BASE_URL + URI, headers=headers, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _normalize_volume(raw: Any) -> int | None:
    """The API returns '< 10' for very low volumes; coerce to 0 (under-10 bucket)
    while passing through real integers as ints."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw).strip()
    if s.startswith("<"):
        return 0
    try:
        return int(s)
    except ValueError:
        return None


def crawl(keywords: list[str], show_detail: bool = True) -> dict[str, Any]:
    """One API call with up to 5 seed keywords. Returns parsed + normalized snapshot.

    The API responds with a `keywordList` containing both the seeds and a
    spread of related keywords. Each entry has monthlyPcQcCnt and
    monthlyMobileQcCnt (the gold).
    """
    env = require_env("NAVER_SEARCH_AD_API_KEY", "NAVER_SEARCH_AD_SECRET_KEY", "NAVER_SEARCH_AD_CUSTOMER_ID")

    if len(keywords) > 5:
        raise ValueError("Naver Search Ad API allows max 5 hint keywords per call")

    raw = _fetch_keywords(
        env["NAVER_SEARCH_AD_API_KEY"],
        env["NAVER_SEARCH_AD_SECRET_KEY"],
        env["NAVER_SEARCH_AD_CUSTOMER_ID"],
        keywords,
        show_detail=show_detail,
    )

    rows: list[dict[str, Any]] = []
    for entry in raw.get("keywordList", []) or []:
        pc = _normalize_volume(entry.get("monthlyPcQcCnt"))
        mo = _normalize_volume(entry.get("monthlyMobileQcCnt"))
        total = (pc or 0) + (mo or 0) if (pc is not None or mo is not None) else None
        rows.append({
            "keyword": entry.get("relKeyword"),
            "monthly_pc": pc,
            "monthly_mobile": mo,
            "monthly_total": total,
            "competition_index": entry.get("compIdx"),
            "click_cost_pc": entry.get("plAvgDepth"),
            "ad_count_pc": entry.get("plAvgPc"),
            "ad_count_mobile": entry.get("plAvgMobile"),
        })

    # Sort by monthly_total desc — the "hottest" keywords first
    rows.sort(key=lambda r: (r.get("monthly_total") or 0), reverse=True)

    payload = {
        "seed_keywords": keywords,
        "total_keywords": len(rows),
        "keywords": rows,
    }
    target = write_snapshot("naver_search_ad", payload)
    payload["_written_to"] = str(target)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Naver Search Ad — real KR search volumes")
    parser.add_argument("--keywords", nargs="+", required=True,
                        help="Seed keywords (max 5)")
    args = parser.parse_args()

    result = crawl(args.keywords)
    print(f"{result['total_keywords']} keywords (incl. related) → {result['_written_to']}")
    if result["keywords"]:
        top = result["keywords"][0]
        print(f"  top: {top['keyword']!r} → {top['monthly_total']:,} monthly searches "
              f"(PC {top['monthly_pc']:,}, mobile {top['monthly_mobile']:,})")


if __name__ == "__main__":
    main()
