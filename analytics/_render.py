"""ASCII chart renderers for the analytics CLIs.

Designed to read well both in a terminal and in Telegram's ``<pre>`` blocks
(monospace, narrow viewport on mobile). Renders use only stdlib, no
matplotlib — dependency-free reports are pasteable into any channel.
"""
from __future__ import annotations

import datetime as _dt
from typing import Sequence


# 8-level block elements used for sparklines. Order matters: lo → hi.
_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"
# Block used for the body of horizontal bars.
_BAR_BLOCK = "█"
_BAR_HALF = "▌"  # 1/2 block, used for visual rounding


def sparkline(values: Sequence[float]) -> str:
    """Compact 8-level unicode sparkline. Empty input → empty string."""
    if not values:
        return ""
    lo = min(values)
    hi = max(values)
    if hi == lo:
        # Constant series: use mid-block for visibility.
        return _SPARK_BLOCKS[len(_SPARK_BLOCKS) // 2] * len(values)
    span = hi - lo
    n = len(_SPARK_BLOCKS) - 1
    return "".join(
        _SPARK_BLOCKS[round((v - lo) / span * n)] for v in values
    )


def bar_chart(
    rows: Sequence[tuple[_dt.date, float]],
    *,
    width: int = 32,
    label_width: int = 5,
    show_value: bool = True,
) -> str:
    """Horizontal bar chart: one row per date, bar length proportional to value.

    The bar is always normalized to the max value in the series; this is
    intentional — the user came to look at *shape*, not absolute units, so a
    self-relative scale is the right default. (For absolute comparison, swap
    in a fixed cap via `--max` on the CLI later.)
    """
    if not rows:
        return "(no data)"
    max_v = max(v for _, v in rows) or 1.0
    out: list[str] = []
    for d, v in rows:
        bar_len_f = (v / max_v) * width
        full = int(bar_len_f)
        half = (bar_len_f - full) >= 0.5
        bar = _BAR_BLOCK * full + (_BAR_HALF if half else "")
        bar = bar.ljust(width)
        prefix = f"{d.isoformat()[5:]:<{label_width}}"
        suffix = f"  {v:>6.1f}" if show_value else ""
        out.append(f"  {prefix}  {bar}{suffix}")
    return "\n".join(out)


def stats_block(
    values: Sequence[float],
    dates: Sequence[_dt.date],
) -> str:
    """Single-block summary statistics: mean/median/min/max/std + peak + trend."""
    if not values or not dates:
        return "  (no data)"
    n = len(values)
    mean = sum(values) / n
    sorted_v = sorted(values)
    if n % 2 == 1:
        median = sorted_v[n // 2]
    else:
        median = (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    mn = min(values)
    mx = max(values)
    var = sum((v - mean) ** 2 for v in values) / n
    std = var ** 0.5

    peak_idx = values.index(mx)
    peak_date = dates[peak_idx]

    delta = values[-1] - values[0]
    if delta > 5:
        trend_label = "↗ rising"
    elif delta < -5:
        trend_label = "↘ falling"
    else:
        trend_label = "→ flat"

    nonzero_days = sum(1 for v in values if v > 0)
    presence = f"{nonzero_days}/{n}일 ({nonzero_days / n:.0%})"

    return (
        f"  mean {mean:.1f}  median {median:.1f}  min {mn:.1f}  max {mx:.1f}  std {std:.1f}\n"
        f"  peak: {peak_date.isoformat()} ({mx:.1f})\n"
        f"  velocity (end−start): {delta:+.1f}  →  {trend_label}\n"
        f"  presence: {presence}"
    )
