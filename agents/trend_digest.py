"""Daily trend digest → Telegram.

Reads the latest crawler + synth snapshots from state/ and composes a
human-readable Telegram message with three sections:

  📺 YouTube trending — top videos with /add <url> lines the user can
     forward to the auto_project bot, which routes /add to Notes_project's
     add_video.yml workflow (transcript + LLM summary + Obsidian vault).
  📊 Trend brief — LLM-generated marketing brief (Breakout/Rising/Declining
     with concrete content angles). Falls back to a deterministic stat
     section when LLM providers are unavailable.
  📱 TikTok leading indicator — top hashtags

This is the meme_project ↔ Notes_project bridge: meme finds what's hot;
the user taps one to deep-dive; Notes_project produces the note.

Run directly (needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in env):
    python -m agents.trend_digest
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
from typing import Any

from crawlers._common import state_dir


def _escape(text: str | None) -> str:
    return _html.escape(text or "", quote=False)


def _latest(source: str) -> dict[str, Any] | None:
    """Read the lexicographically last JSON file under state/<source>/."""
    d = state_dir(source)
    files = sorted(d.glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def _latest_two(source: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (latest, previous). Previous is None if fewer than 2 snapshots."""
    d = state_dir(source)
    files = sorted(d.glob("*.json"))
    latest = json.loads(files[-1].read_text(encoding="utf-8")) if files else None
    previous = json.loads(files[-2].read_text(encoding="utf-8")) if len(files) >= 2 else None
    return latest, previous


def _top_youtube_videos(snap: dict | None, k: int = 8) -> list[dict[str, Any]]:
    """Flatten videos across categories, sort by view_count desc, take top k."""
    if not snap:
        return []
    rows: list[dict[str, Any]] = []
    for vids in (snap.get("videos_by_category") or {}).values():
        for v in vids:
            rows.append(v)
    rows.sort(key=lambda r: r.get("view_count") or 0, reverse=True)
    return rows[:k]


def _enrich_keywords_with_delta(
    latest: dict | None,
    previous: dict | None,
    k: int = 5,
) -> list[dict[str, Any]]:
    """Take top-k keywords from latest synth snapshot, attach delta vs previous.

    Each returned dict has the original keyword fields plus:
      delta_label: '🆕'  (new this run)  /  '↑+N.N' / '↓-N.N' / '→' (stable)
      delta_value: float | None  (raw score delta, None if new)
    """
    if not latest:
        return []
    prev_map = {
        kw["keyword"]: kw.get("trend_score", 0.0)
        for kw in ((previous or {}).get("keywords") or [])
        if kw.get("keyword")
    }
    out: list[dict[str, Any]] = []
    for kw in (latest.get("keywords") or [])[:k]:
        name = kw.get("keyword")
        score = kw.get("trend_score") or 0.0
        prev = prev_map.get(name)
        if prev is None:
            label = "🆕"
            delta = None
        else:
            d = score - prev
            delta = round(d, 1)
            if d > 0.5:
                label = f"↑+{d:.1f}"
            elif d < -0.5:
                label = f"↓{d:.1f}"
            else:
                label = "→"
        out.append({**kw, "delta_label": label, "delta_value": delta})
    return out


def _top_synth_keywords(snap: dict | None, k: int = 5) -> list[dict[str, Any]]:
    """Plain top-k without delta (used by tests that don't care about deltas)."""
    if not snap:
        return []
    return (snap.get("keywords") or [])[:k]


def _top_tiktok_hashtags(snap: dict | None, k: int = 3) -> list[dict[str, Any]]:
    if not snap:
        return []
    return (snap.get("hashtags") or [])[:k]


# --- Markdown → Telegram-HTML converter ---------------------------------
# trend_brief emits markdown for archival; Telegram needs its narrow HTML
# subset. This converter handles only what the brief actually produces.

_MD_HEADER_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\w)_(.+?)_(?!\w)")


def _md_to_telegram_html(md: str) -> str:
    """Convert the brief's markdown to the limited Telegram HTML subset."""
    text = _html.escape(md, quote=False)
    text = _MD_HEADER_RE.sub(r"<b>\1</b>", text)
    text = _MD_BOLD_RE.sub(r"<b>\1</b>", text)
    text = _MD_ITALIC_RE.sub(r"<i>\1</i>", text)
    return text


def _strip_brief_header(body: str) -> str:
    """Drop the brief's own "📊 트렌드 브리프 — date" line so the digest's
    parent header is the only top-level heading.
    """
    lines = body.splitlines()
    drop = 0
    for line in lines:
        s = line.strip()
        if not s:
            drop += 1
            continue
        if s.startswith("📊 트렌드 브리프") or s.startswith("# 📊"):
            drop += 1
            continue
        break
    return "\n".join(lines[drop:]).lstrip()


def compose(
    yt_snap: dict | None,
    synth_snap: dict | None,
    tt_snap: dict | None,
    brief_body: str | None = None,
    yt_top_n: int = 8,
    keyword_top_n: int = 5,
    tt_top_n: int = 3,
) -> str:
    """Compose the HTML Telegram message body. Sections with no data are skipped.

    `brief_body` is the markdown output from agents.trend_brief. When provided,
    it replaces the legacy "교차 출처 핫 키워드" block. When None (test paths
    that don't run momentum yet), the legacy top-keyword list is rendered
    instead so the digest never goes blank.
    """
    lines: list[str] = ["<b>🔥 오늘의 트렌드 다이제스트</b>"]

    yt_videos = _top_youtube_videos(yt_snap, k=yt_top_n)
    if yt_videos:
        lines.append("")
        lines.append("<b>📺 YouTube 트렌딩</b> <i>(탭/포워드로 노트화)</i>")
        for v in yt_videos:
            title = _escape((v.get("title") or "")[:70])
            channel = _escape(v.get("channel_title") or "")
            views = v.get("view_count") or 0
            vid = v.get("video_id") or ""
            url = f"https://youtu.be/{vid}" if vid else ""
            lines.append(f"\n• <b>{title}</b>")
            lines.append(f"  <i>{channel}</i> · {views:,} views")
            if url:
                lines.append(f"  <code>/add {url}</code>")

    if brief_body:
        lines.append("")
        lines.append("<b>📊 트렌드 브리프</b>")
        lines.append(_md_to_telegram_html(_strip_brief_header(brief_body)))
    else:
        keywords = _top_synth_keywords(synth_snap, k=keyword_top_n)
        if keywords:
            lines.append("")
            lines.append("<b>🔑 교차 출처 핫 키워드</b>")
            for kw in keywords:
                name = _escape(kw.get("keyword") or "")
                score = kw.get("trend_score") or 0
                srcs = ", ".join(_escape(s) for s in (kw.get("sources") or []))
                delta = kw.get("delta_label")
                head = f"<b>{name}</b> ({score:.1f}){' ' + delta if delta else ''}"
                lines.append(f"• {head} — <i>{srcs}</i>")

    tt = _top_tiktok_hashtags(tt_snap, k=tt_top_n)
    if tt:
        lines.append("")
        lines.append("<b>📱 TikTok 선행 지표</b>")
        for h in tt:
            name = _escape(h.get("hashtag_name") or "")
            vc = h.get("view_count") or 0
            industry = _escape(h.get("industry") or "")
            tail = f" · {industry}" if industry else ""
            lines.append(f"• #{name} · {vc:,} views{tail}")

    return "\n".join(lines)


def _send_telegram(message: str) -> bool:
    """Lazy import so tests don't need auto_project installed."""
    from auto_project.notify import telegram
    return telegram(message, parse_mode="HTML")


def _generate_brief() -> dict[str, Any] | None:
    """Compute momentum + confidence + brief on the fly.

    Each call writes fresh snapshots under state/, then asks trend_brief to
    render the marketing-ready markdown. Errors degrade gracefully — the
    digest still goes out with the legacy keyword section if anything here
    raises (e.g. missing module during partial rollout, momentum input gap).
    """
    try:
        from synth import momentum, confidence
        from agents import trend_brief
        from crawlers._common import write_snapshot as _ws
    except ImportError:
        return None
    try:
        _ws("momentum", momentum.compute())
        _ws("confidence", confidence.compute())
        return trend_brief.generate()
    except Exception:
        return None


def run(dry_run: bool = False) -> dict[str, Any]:
    """Build the message; send unless dry_run. Returns a result dict.

    For synth, reads the latest TWO snapshots so we can show per-keyword
    deltas (↑/↓/→/🆕) in the message — gives the digest fresh information
    every run even when the underlying trending content is similar.
    """
    yt = _latest("youtube_trending")
    synth, synth_prev = _latest_two("synth_hot_keywords")
    tt = _latest("tiktok_creative")

    # If we have a synth snapshot, enrich top keywords with delta vs previous.
    if synth:
        enriched = _enrich_keywords_with_delta(synth, synth_prev, k=5)
        synth = {**synth, "keywords": enriched}

    sources_present = [name for name, snap in [
        ("youtube_trending", yt), ("synth_hot_keywords", synth), ("tiktok_creative", tt),
    ] if snap]

    # If there's literally no data, don't fire a Telegram message — the brief
    # fallback would otherwise emit a contentless "특이 신호 없음" line every
    # cron tick.
    if not sources_present:
        return {"sent": False, "reason": "no snapshots", "sources_present": []}

    # Generate the marketing brief (momentum + confidence + LLM/fallback).
    # Done inline so the digest workflow doesn't need a separate step. Failure
    # is non-fatal — we still send the digest with the legacy keyword section.
    brief = _generate_brief()
    brief_body = brief["body"] if brief and brief.get("body") else None
    if brief_body:
        sources_present.append(f"brief({brief.get('source', '?')})")

    message = compose(yt, synth, tt, brief_body=brief_body)

    if dry_run:
        return {"sent": False, "reason": "dry_run", "sources_present": sources_present, "message": message}

    ok = _send_telegram(message)
    return {"sent": bool(ok), "sources_present": sources_present, "message_len": len(message)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily trend digest to Telegram")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compose the message and print it; don't send.")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run)
    if args.dry_run:
        print(result.get("message", ""))
    print(f"\n→ sent={result['sent']}  sources={result.get('sources_present')}"
          + (f"  reason={result.get('reason')}" if result.get("reason") else ""))


if __name__ == "__main__":
    main()
