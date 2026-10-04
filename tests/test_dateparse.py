"""Tests for the date parser shared by analytics CLIs."""
from __future__ import annotations

import datetime as _dt

import pytest

from analytics._dateparse import parse_date, parse_range


FIXED_TODAY = _dt.date(2026, 5, 22)


def test_iso_yyyy_mm_dd():
    assert parse_date("2026-04-16", today=FIXED_TODAY) == _dt.date(2026, 4, 16)


def test_dotted_form():
    assert parse_date("2026.04.16", today=FIXED_TODAY) == _dt.date(2026, 4, 16)


def test_slashed_form():
    assert parse_date("2026/04/16", today=FIXED_TODAY) == _dt.date(2026, 4, 16)


def test_compact_form():
    assert parse_date("20260416", today=FIXED_TODAY) == _dt.date(2026, 4, 16)


def test_today_alias():
    assert parse_date("today", today=FIXED_TODAY) == FIXED_TODAY
    assert parse_date("TODAY", today=FIXED_TODAY) == FIXED_TODAY


def test_yesterday_alias():
    assert parse_date("yesterday", today=FIXED_TODAY) == _dt.date(2026, 5, 21)


@pytest.mark.parametrize("phrase,delta", [
    ("last7d", 7),
    ("last30d", 30),
    ("last 7 d", 7),  # whitespace-tolerant
])
def test_last_n_days(phrase, delta):
    assert parse_date(phrase, today=FIXED_TODAY) == FIXED_TODAY - _dt.timedelta(days=delta)


def test_invalid_raises():
    with pytest.raises(ValueError, match="unrecognized"):
        parse_date("nope", today=FIXED_TODAY)


def test_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        parse_date("", today=FIXED_TODAY)


def test_parse_range_swaps_when_inverted():
    a, b = parse_range("2026-05-03", "2026-04-16", today=FIXED_TODAY)
    assert a == _dt.date(2026, 4, 16) and b == _dt.date(2026, 5, 3)


def test_parse_range_end_defaults_to_today():
    a, b = parse_range("2026-04-16", None, today=FIXED_TODAY)
    assert a == _dt.date(2026, 4, 16) and b == FIXED_TODAY
