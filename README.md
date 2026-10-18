# meme_project

Multi-platform trend & content radar. Built on top of the
[`auto_project`](https://github.com/Sweet-Butters/auto_project) framework
(zero-cost autonomous agents via GitHub Actions).

## What it does

Crawls and synthesizes "what's hot right now" signals across platforms that
expose data YouTube hides:

1. **YouTube trending** — official Data API + yt-dlp metadata enrichment
2. **Keyword → hot videos** — yt-dlp search + view-count enrichment, also
   triggerable from the Telegram bot
3. **Sector radar** — pytrends (Google Trends, YouTube property) + TikTok
   Creative Center hashtag/sound trends + Naver DataLab + Naver Search Ad
   API (real KR search volumes)

Outputs structured JSON snapshots (time-series) under `state/`, optionally
LLM-summarized via `auto_project.llm`.

## Stack

| Layer | Tool |
|-------|------|
| Agent runner / state / LLM router / Telegram alerts | `auto_project >= 0.3.0` |
| YouTube metadata + captions + comments | `yt-dlp` |
| YouTube Data API (trending, search) | `google-api-python-client` |
| Google Trends (YouTube property) | `pytrends` |
| TikTok Creative Center | scraper (TBD) |
| Naver DataLab + Search Ad API | `requests` (official endpoints) |

## Status

**v0.2 — all 6 crawlers + cross-source synth + trend digest + 4 workflows. Mock-tested with 37 passing tests.** Awaiting API keys (`YOUTUBE_API_KEY`, `NAVER_*`, `TELEGRAM_*` via GitHub Actions secrets) to enable real runs.

**Runner**: all workflows run on cloud `ubuntu-latest`. The project's data sources are mostly official APIs that work fine from cloud IPs, so no self-hosted runner is needed. Self-hosted is available as an opt-in upgrade for `crawl_keyword.yml` (yt-dlp search) if cloud 403s become frequent — see [`scripts/optional/README.md`](scripts/optional/README.md).

| Module | Purpose | Cost | Status |
|--------|---------|------|--------|
| `crawlers/youtube_trending` | Official trending chart per region/category | 1 unit/cat | ✅ |
| `crawlers/youtube_keyword` | yt-dlp search + Data API view-count enrichment | 0 (no enrich) or 1 unit/50 ids | ✅ |
| `crawlers/pytrends_sector` | Google Trends YouTube property by sector | Free, rate-limited | ✅ |
| `crawlers/naver_datalab` | Official KR search ratios + demographic breakdowns | Free, ~25k/day | ✅ |
| `crawlers/naver_search_ad` | **Real KR monthly volumes (absolute)** | Free | ✅ |
| `crawlers/tiktok_creative` | Trending hashtags + sounds (cross-platform leading indicator) | Free, fragile | ✅ |
| `synth/hot_keywords` | Cross-source min-max + weights + bonus rank | Free | ✅ |
| `agents/trend_digest` | Telegram digest with `/add <url>` lines → Notes_project bridge | Free | ✅ |

Workflows:
- `crawl_trending.yml` — every 6h (YT + TikTok + synth)
- `crawl_sectors.yml` — daily 06:00 KST (Naver x2 + pytrends + synth)
- `crawl_keyword.yml` — `workflow_dispatch` (bot-triggerable)
- `digest.yml` — daily 07:00 KST, sends digest to Telegram

## Notes_project bridge

The `digest.yml` workflow sends a daily Telegram message including
`/add <youtube-url>` lines for the top trending videos. Forwarding any
of those lines to the `auto_project` Cloud Run bot triggers
`Notes_project/add_video.yml`, which produces a transcript + LLM
summary in the user's Obsidian vault.

Net effect: meme_project surfaces what's hot → user taps to deep-dive →
Notes_project produces the note. One-tap UX.

Next: set GitHub Actions secrets, kick off `crawl_trending` once manually, inspect state/ output, then enable the cron triggers.

## Tests

```bash
python3 -m pytest -q tests/
```

All current tests are mock-only (no network). 25 tests pass.
