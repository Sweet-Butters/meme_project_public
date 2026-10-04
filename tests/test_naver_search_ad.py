"""Mock-only tests for naver_search_ad (incl. signing)."""
from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from crawlers import naver_search_ad
from crawlers._common import MissingEnvError


FAKE_API_RESPONSE = {
    "keywordList": [
        {
            "relKeyword": "AI",
            "monthlyPcQcCnt": 12345,
            "monthlyMobileQcCnt": 67890,
            "compIdx": "높음",
            "plAvgDepth": 5,
            "plAvgPc": 10,
            "plAvgMobile": 8,
        },
        {
            "relKeyword": "AI 강의",
            "monthlyPcQcCnt": "< 10",  # bucketed by Naver
            "monthlyMobileQcCnt": 200,
            "compIdx": "낮음",
            "plAvgDepth": 1,
            "plAvgPc": 0,
            "plAvgMobile": 1,
        },
        {
            "relKeyword": "MLOps",
            "monthlyPcQcCnt": 1000,
            "monthlyMobileQcCnt": 2000,
            "compIdx": "중간",
        },
    ]
}


def test_sign_matches_manual_hmac():
    secret = "shhh"
    ts = "1234567890"
    sig = naver_search_ad._sign(secret, ts, "GET", "/keywordstool")
    expected = base64.b64encode(
        hmac.new(b"shhh", b"1234567890.GET./keywordstool", hashlib.sha256).digest()
    ).decode("utf-8")
    assert sig == expected


def test_normalize_volume_handles_bucketed_low():
    assert naver_search_ad._normalize_volume(123) == 123
    assert naver_search_ad._normalize_volume("123") == 123
    assert naver_search_ad._normalize_volume("< 10") == 0
    assert naver_search_ad._normalize_volume("<10") == 0
    assert naver_search_ad._normalize_volume(None) is None


def test_crawl_writes_sorted_snapshot(monkeypatch, tmp_state, read_snapshot):
    monkeypatch.setenv("NAVER_SEARCH_AD_API_KEY", "key")
    monkeypatch.setenv("NAVER_SEARCH_AD_SECRET_KEY", "secret")
    monkeypatch.setenv("NAVER_SEARCH_AD_CUSTOMER_ID", "111")

    def fake_fetch(api_key, secret, customer_id, keywords, show_detail=True, timeout=30):
        assert keywords == ["AI"]
        return FAKE_API_RESPONSE

    monkeypatch.setattr(naver_search_ad, "_fetch_keywords", fake_fetch)

    result = naver_search_ad.crawl(["AI"])

    assert result["total_keywords"] == 3
    # Sort by monthly_total desc:
    #   AI:       12345 + 67890 = 80235
    #   MLOps:    1000  + 2000  = 3000
    #   AI 강의:   0     + 200   = 200
    ids = [r["keyword"] for r in result["keywords"]]
    assert ids == ["AI", "MLOps", "AI 강의"]
    assert result["keywords"][0]["monthly_total"] == 80235
    assert result["keywords"][2]["monthly_pc"] == 0  # bucketed "< 10"

    snap = read_snapshot(tmp_state / "naver_search_ad")
    assert snap["seed_keywords"] == ["AI"]


def test_crawl_rejects_more_than_5_seeds(monkeypatch, tmp_state):
    monkeypatch.setenv("NAVER_SEARCH_AD_API_KEY", "k")
    monkeypatch.setenv("NAVER_SEARCH_AD_SECRET_KEY", "s")
    monkeypatch.setenv("NAVER_SEARCH_AD_CUSTOMER_ID", "c")
    with pytest.raises(ValueError, match="max 5"):
        naver_search_ad.crawl(["a", "b", "c", "d", "e", "f"])


def test_crawl_requires_env(monkeypatch, tmp_state):
    for v in ("NAVER_SEARCH_AD_API_KEY", "NAVER_SEARCH_AD_SECRET_KEY", "NAVER_SEARCH_AD_CUSTOMER_ID"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(MissingEnvError):
        naver_search_ad.crawl(["AI"])
