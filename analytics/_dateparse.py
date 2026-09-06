"""Flexible date parsing for the analytics CLIs.

Accepts the conventional ISO forms plus a small set of relative shortcuts.
Centralized so the same parser is reused by every entry point — no surprises
when one CLI accepts `last7d` but another doesn't.
"""
from __future__ import annotations

import datetime as _dt
import re


_ISO_FORMATS = ["%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d"]
_LAST_N = re.compile(r"^last\s*(\d+)\s*d$", re.IGNORECASE)


def parse_date(value: str, *, today: _dt.date | None = None) -> _dt.date:
    """Parse a user-supplied date string into a date.

    Recognizes: ISO (YYYY-MM-DD), dotted (YYYY.MM.DD), slashed (YYYY/MM/DD),
    compact (YYYYMMDD), plus `today`, `yesterday`, `last7d`/`last30d`.
    """
    s = (value or "").strip().lower()
    if not s:
        raise ValueError("empty date")
    now = today or _dt.date.today()
    if s in {"today", "now"}:
        return now
    if s in {"yesterday"}:
        return now - _dt.timedelta(days=1)
    m = _LAST_N.match(s)
    if m:
        return now - _dt.timedelta(days=int(m.group(1)))
    for fmt in _ISO_FORMATS:
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"unrecognized date '{value}'. Try YYYY-MM-DD, YYYY.MM.DD, "
        f"YYYY/MM/DD, YYYYMMDD, today, yesterday, last7d, last30d."
    )


def parse_range(start: str, end: str | None, *,
                today: _dt.date | None = None) -> tuple[_dt.date, _dt.date]:
    """Parse a start/end pair. `end` may be None → defaults to today.

    Auto-swaps when end < start so users can't get an empty range from a
    typo. Same-day ranges are kept as-is.
    """
    a = parse_date(start, today=today)
    b = parse_date(end, today=today) if end else (today or _dt.date.today())
    if b < a:
        a, b = b, a
    return a, b
