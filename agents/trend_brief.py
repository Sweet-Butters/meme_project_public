"""LLM-generated daily marketing brief.

Takes the latest momentum + confidence snapshots, plus the current synth
ranking and raw evidence (top YouTube titles, top TikTok hashtags), and
produces a four-section Korean markdown brief that a marketing/sales lead
can scan in under a minute:

    🔥 폭발     — fresh breakouts with z-score & day-1 content angle
    📈 상승     — sustained risers
    📉 주의     — declines / noisy signals
    💡 추천 액션 — concrete next steps

The numerics (z, score, source list) stay in parentheses so a skeptical
reader can audit the narrative without leaving the message.

If every LLM provider fails, we fall back to a deterministic stat-only
brief composed from the same payload — the digest never silently drops.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from crawlers._common import state_dir, write_snapshot


# How many items to highlight in each section of the brief.
N_BREAKOUTS = 3
N_RISERS = 5
N_DECLINERS = 3
# Evidence snippets attached to the LLM payload (titles aid grounding).
N_YT_EVIDENCE = 5
N_TT_EVIDENCE = 5


def _latest(source: str) -> dict[str, Any] | None:
    d = state_dir(source)
    files = sorted(d.glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def _confidence_lookup(conf_snap: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Index confidence rows by keyword for O(1) joins."""
    if not conf_snap:
        return {}
    return {r["keyword"]: r for r in (conf_snap.get("keywords") or []) if r.get("keyword")}


def _pick(rows: list[dict[str, Any]], label: str, n: int) -> list[dict[str, Any]]:
    """Top-N rows of a given momentum label (already sorted by momentum.compute)."""
    return [r for r in rows if r.get("label") == label][:n]


def build_payload() -> dict[str, Any]:
    """Assemble the structured payload the LLM (or fallback) renders."""
    mom = _latest("momentum") or {}
    conf = _latest("confidence")
    yt = _latest("youtube_trending") or {}
    tt = _latest("tiktok_creative") or {}

    rows = mom.get("keywords") or []
    conf_map = _confidence_lookup(conf)

    def _attach_conf(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for it in items:
            c = conf_map.get(it["keyword"], {})
            out.append({
                **it,
                "confidence_score": c.get("confidence_score"),
                "n_sources_top10": c.get("n_sources_top10"),
            })
        return out

    breakouts = _attach_conf(_pick(rows, "🔥 Breakout", N_BREAKOUTS))
    risers = _attach_conf(_pick(rows, "📈 Rising", N_RISERS))
    decliners = _attach_conf(_pick(rows, "📉 Declining", N_DECLINERS))
    # When breakouts/risers are scarce — first ~7 days, or just a quiet day —
    # surface the highest-scoring 🆕 New keywords (these are interesting by
    # virtue of being NEW even if we don't have stats yet) and the strong
    # 💤 Steady items as "always-on" reference points.
    new_today: list[dict[str, Any]] = []
    always_on: list[dict[str, Any]] = []
    if not breakouts and not risers:
        news = sorted(_pick(rows, "🆕 New", 8),
                      key=lambda r: -r["score_today"])
        new_today = _attach_conf([r for r in news if r["score_today"] >= 30][:5])
        steady = sorted(_pick(rows, "💤 Steady", 8),
                        key=lambda r: -r["score_today"])
        always_on = _attach_conf([r for r in steady if r["score_today"] >= 50][:3])

    # Evidence: top YouTube titles + TikTok hashtags. Keep short.
    yt_titles = []
    for vids in (yt.get("videos_by_category") or {}).values():
        for v in vids:
            t = (v.get("title") or "").strip()
            if t:
                yt_titles.append(t)
    yt_titles = yt_titles[:N_YT_EVIDENCE]
    tt_hashtags = [h.get("hashtag_name") for h in (tt.get("hashtags") or [])[:N_TT_EVIDENCE]
                   if h.get("hashtag_name")]

    return {
        "as_of": mom.get("as_of"),
        "history_days_available": mom.get("history_days_available", 0),
        "label_counts": mom.get("label_counts", {}),
        "breakouts": breakouts,
        "risers": risers,
        "decliners": decliners,
        "new_today": new_today,
        "always_on": always_on,
        "evidence": {
            "youtube_titles": yt_titles,
            "tiktok_hashtags": tt_hashtags,
        },
    }


SYSTEM_PROMPT = """\
You are a Korean marketing data analyst preparing a one-page daily trend brief.
Audience: 마케팅 매니저, 콘텐츠 제작자, 세일즈 리드.

OUTPUT RULES (반드시 지킬 것)
1. 한국어 마크다운. 네 개 섹션: 🔥 폭발 / 📈 상승 / 📉 주의 / 💡 추천 액션
2. 각 항목은 두 줄:
   - **키워드** (괄호로 핵심 통계: 예 z=2.4, 신뢰도 87%)
   - 한 줄짜리 "왜 지금" + 한 줄짜리 콘텐츠/액션 제안
3. 추천 액션 섹션은 1~3개 번호 매긴 실행 가능한 다음 단계만.
4. 숫자는 의사결정에 의미가 있을 때만 노출. "아마", "추정" 같은 헷지 금지.
5. INPUT에 없는 키워드/숫자 절대 만들지 말 것.
6. 데이터가 적으면 (history_days_available < 3) 그 사실을 한 줄 노트로 명시.

전체 길이는 350~500자 한국어로 압축. 본부장이 5분 안에 읽고 결정할 수준.
"""


def _format_payload_for_llm(payload: dict[str, Any]) -> str:
    """Render the structured payload as a compact, LLM-friendly block."""
    def _fmt_row(r: dict[str, Any]) -> str:
        stats = []
        if r.get("z_score") is not None:
            stats.append(f"z={r['z_score']:+.2f}")
        if r.get("velocity") is not None:
            stats.append(f"vel={r['velocity']:+.1f}")
        if r.get("acceleration") is not None:
            stats.append(f"acc={r['acceleration']:+.1f}")
        if r.get("score_today") is not None:
            stats.append(f"score={r['score_today']:.0f}")
        if r.get("confidence_score") is not None:
            stats.append(f"conf={r['confidence_score']:.2f}")
        if r.get("n_sources_top10") is not None:
            stats.append(f"top10_in={r['n_sources_top10']}/srcs")
        srcs = ",".join(r.get("sources") or [])
        return f"- {r['keyword']}  ({'; '.join(stats)})  [{srcs}]"

    lines = [
        f"AS_OF: {payload.get('as_of')}",
        f"HISTORY_DAYS_AVAILABLE: {payload.get('history_days_available')}",
        f"LABEL_COUNTS: {payload.get('label_counts')}",
        "",
        "BREAKOUTS:",
        *(_fmt_row(r) for r in payload.get("breakouts", [])),
        "",
        "RISERS:",
        *(_fmt_row(r) for r in payload.get("risers", [])),
        "",
        "DECLINERS:",
        *(_fmt_row(r) for r in payload.get("decliners", [])),
    ]
    if payload.get("new_today"):
        lines.extend(["", "NEW_TODAY (fallback when stats aren't ready yet):"])
        lines.extend(_fmt_row(r) for r in payload["new_today"])
    if payload.get("always_on"):
        lines.extend(["", "ALWAYS_ON (fallback when data is thin):"])
        lines.extend(_fmt_row(r) for r in payload["always_on"])

    ev = payload.get("evidence", {})
    if ev.get("youtube_titles"):
        lines.extend(["", "EVIDENCE_YOUTUBE_TITLES:"])
        lines.extend(f"- {t}" for t in ev["youtube_titles"])
    if ev.get("tiktok_hashtags"):
        lines.extend(["", "EVIDENCE_TIKTOK_HASHTAGS:"])
        lines.extend(f"- #{h}" for h in ev["tiktok_hashtags"])
    return "\n".join(lines)


def _render_stat_fallback(payload: dict[str, Any]) -> str:
    """Deterministic brief used when LLM is unavailable.

    Same four sections, no LLM, no narrative — just the data laid out.
    Marketing can still act on it; it's just less polished.
    """
    def _line(r: dict[str, Any]) -> str:
        bits = [f"**{r['keyword']}**"]
        stats = []
        if r.get("z_score") is not None and r["label"] != "🆕 New":
            stats.append(f"z={r['z_score']:+.1f}")
        if r.get("confidence_score") is not None:
            stats.append(f"신뢰도 {int(r['confidence_score'] * 100)}%")
        if stats:
            bits.append(f"({', '.join(stats)})")
        srcs = ", ".join(r.get("sources") or [])
        if srcs:
            bits.append(f"— {srcs}")
        return "  • " + " ".join(bits)

    as_of = payload.get("as_of") or "—"
    out = [f"📊 트렌드 브리프 — {as_of}", ""]

    if payload.get("history_days_available", 0) < 3:
        out.append(f"_(데이터 누적 {payload.get('history_days_available', 0)}일 — "
                   "통계 신뢰도는 1주 후부터 안정화)_")
        out.append("")

    sections = [
        ("🔥 폭발 (Breakout)", payload.get("breakouts", [])),
        ("📈 상승 (Rising)", payload.get("risers", [])),
        ("📉 주의 (Declining)", payload.get("decliners", [])),
    ]
    # Surface "new today" items when there's no breakout/rising to talk about —
    # this is what the early days of the project look like.
    if payload.get("new_today"):
        sections.append(("🆕 신규 (Just appeared)", payload["new_today"]))
    if payload.get("always_on"):
        sections.append(("💤 꾸준 (Always-on)", payload["always_on"]))

    any_content = False
    for title, items in sections:
        if not items:
            continue
        any_content = True
        out.append(f"## {title}")
        out.extend(_line(r) for r in items)
        out.append("")

    if not any_content:
        out.append("_오늘은 특이 신호 없음 — 일상 트렌드만 관측됨._")

    return "\n".join(out).rstrip()


def _call_llm(payload: dict[str, Any]) -> str | None:
    """Try the LLM router. Returns None if every provider fails."""
    try:
        from auto_project.llm import call as llm_call
    except ImportError:
        return None

    prompt = (
        "INPUT (실제 측정값):\n"
        + _format_payload_for_llm(payload)
        + "\n\n"
        "위 INPUT만 사용해서 명세대로 마크다운 브리프 작성."
    )
    try:
        return llm_call(prompt, system=SYSTEM_PROMPT, timeout=60)
    except Exception:
        # Any provider chain exhaustion → fall back. Caller still gets a brief.
        return None


def generate(force_fallback: bool = False) -> dict[str, Any]:
    """Build the brief. Returns metadata + markdown body."""
    payload = build_payload()
    if force_fallback:
        body = _render_stat_fallback(payload)
        source = "stat_fallback (forced)"
    else:
        llm_body = _call_llm(payload)
        if llm_body:
            body = llm_body.strip()
            source = "llm"
        else:
            body = _render_stat_fallback(payload)
            source = "stat_fallback (llm unavailable)"

    out = {
        "as_of": payload.get("as_of"),
        "source": source,
        "history_days_available": payload.get("history_days_available", 0),
        "label_counts": payload.get("label_counts", {}),
        "body": body,
    }
    target = write_snapshot("trend_brief", out)
    out["_written_to"] = str(target)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily trend brief")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip LLM and emit the deterministic stat fallback.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the brief to stdout; don't write a snapshot.")
    args = parser.parse_args()

    if args.dry_run:
        payload = build_payload()
        if args.no_llm:
            print(_render_stat_fallback(payload))
        else:
            body = _call_llm(payload) or _render_stat_fallback(payload)
            print(body)
        return

    result = generate(force_fallback=args.no_llm)
    print(f"brief → {result['_written_to']}  (source={result['source']})")
    print()
    print(result["body"])


if __name__ == "__main__":
    main()
