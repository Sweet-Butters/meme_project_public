"""Mock-only tests for naver_datalab."""
from __future__ import annotations

import pytest

from crawlers import naver_datalab
from crawlers._common import MissingEnvError


FAKE_API_RESPONSE = {
    "startDate": "2026-04-01",
    "endDate": "2026-05-01",
    "timeUnit": "date",
    "results": [
        {
            "title": "g1",
            "keywords": ["AI", "딥러닝"],
            "data": [
                {"period": "2026-04-01", "ratio": 12.3},
                {"period": "2026-04-02", "ratio": 45.7},
            ],
        }
    ],
}


def test_crawl_writes_main(monkeypatch, tmp_state, read_snapshot):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")

    calls = []
    def fake_fetch(cid, csec, groups, start, end, tu, **kwargs):
        calls.append({"start": start, "end": end, "tu": tu, **kwargs})
        return FAKE_API_RESPONSE

    monkeypatch.setattr(naver_datalab, "_fetch_datalab", fake_fetch)

    groups = [{"groupName": "g1", "keywords": ["AI", "딥러닝"]}]
    result = naver_datalab.crawl(groups, "2026-04-01", "2026-05-01")

    assert result["start_date"] == "2026-04-01"
    assert result["main"]["results"][0]["title"] == "g1"
    assert result["breakdowns"] == {}
    assert len(calls) == 1

    snap = read_snapshot(tmp_state / "naver_datalab")
    assert snap["_meta"]["source"] == "naver_datalab"


def test_crawl_with_breakdowns(monkeypatch, tmp_state):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")

    calls = []
    def fake_fetch(cid, csec, groups, start, end, tu, **kwargs):
        calls.append(kwargs)
        return {"results": [], **kwargs}
    monkeypatch.setattr(naver_datalab, "_fetch_datalab", fake_fetch)

    groups = [{"groupName": "g1", "keywords": ["AI"]}]
    result = naver_datalab.crawl(
        groups, "2026-04-01", "2026-05-01",
        breakdown_by=["device:pc", "device:mo", "gender:f"],
    )

    assert "device:pc" in result["breakdowns"]
    assert "device:mo" in result["breakdowns"]
    assert "gender:f" in result["breakdowns"]
    # 1 main + 3 breakdowns
    assert len(calls) == 4
    assert calls[1] == {"device": "pc"}
    assert calls[3] == {"gender": "f"}


def test_requires_env(monkeypatch, tmp_state):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    with pytest.raises(MissingEnvError):
        naver_datalab.crawl([{"groupName": "g1", "keywords": ["x"]}])


def test_default_date_range_is_30_days():
    s, e = naver_datalab._default_date_range(30)
    # Just sanity: both ISO-format strings, end >= start
    assert len(s) == 10 and len(e) == 10
    assert e >= s


def test_build_keyword_groups_per_keyword_by_default():
    groups = naver_datalab._build_keyword_groups(["AI", "딥러닝", "ChatGPT"])
    # One group per keyword → DataLab returns a discriminating series each.
    assert len(groups) == 3
    assert all(len(g["keywords"]) == 1 for g in groups)
    assert [g["keywords"][0] for g in groups] == ["AI", "딥러닝", "ChatGPT"]
    # groupName == the keyword, so the synth maps the series back per keyword.
    assert groups[0] == {"groupName": "AI", "keywords": ["AI"]}


def test_build_keyword_groups_single_group_is_legacy_lump():
    groups = naver_datalab._build_keyword_groups(["AI", "딥러닝"], single_group=True)
    assert groups == [{"groupName": "g1", "keywords": ["AI", "딥러닝"]}]


def test_build_keyword_groups_strips_and_rejects_empty():
    assert naver_datalab._build_keyword_groups(["  AI  ", "", "딥러닝"]) == [
        {"groupName": "AI", "keywords": ["AI"]},
        {"groupName": "딥러닝", "keywords": ["딥러닝"]},
    ]
    with pytest.raises(ValueError):
        naver_datalab._build_keyword_groups([])
    with pytest.raises(ValueError):
        naver_datalab._build_keyword_groups(["   "])


def test_build_keyword_groups_caps_at_datalab_limit():
    too_many = [f"kw{i}" for i in range(naver_datalab.DATALAB_MAX_GROUPS + 1)]
    with pytest.raises(ValueError):
        naver_datalab._build_keyword_groups(too_many)
    # single_group bypasses the per-group cap (it's just one group).
    assert len(naver_datalab._build_keyword_groups(too_many, single_group=True)) == 1
