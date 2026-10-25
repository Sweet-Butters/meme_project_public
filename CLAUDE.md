# meme_project — Claude context (SSOT)

**Public sanitized snapshot** of a personal multi-platform trend/content radar (see Sweet-Butters/meme_project). Multi-platform trend/content radar built on the
public [`auto_project`](https://github.com/Sweet-Butters/auto_project)
framework. Project-specific identifiers, secrets, agents, prompts, and
state all live **here**, never in `auto_project`.

## Quick Start

```bash
git clone git@github.com:Sweet-Butters/meme_project.git
cd meme_project
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Required env vars (set via GitHub Actions secrets, or `.env` for local —
**never commit `.env`**):

| Var | Purpose | Source |
|-----|---------|--------|
| `YOUTUBE_API_KEY` | YouTube Data API v3 | Google Cloud Console → APIs & Services → Credentials |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | Naver DataLab Open API | developers.naver.com |
| `NAVER_SEARCH_AD_API_KEY` / `..._SECRET` / `..._CUSTOMER_ID` | Naver Search Ad API (real KR search volumes) | searchad.naver.com → 도구 → API |
| `GEMINI_API_KEY` / `GROQ_API_KEY` / `CEREBRAS_API_KEY` | LLM router fallback chain (via `auto_project.llm`) | each provider's console |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alerts via `auto_project.notify` | BotFather + getUpdates |

## Planned architecture

```
meme_project/
├── crawlers/
│   ├── youtube_trending.py    # videos.list?chart=mostPopular per region/category
│   ├── youtube_keyword.py     # yt-dlp search + Data API enrichment
│   ├── pytrends_sector.py     # Google Trends YouTube property by category
│   ├── tiktok_creative.py     # Creative Center scraper (hashtags + sounds)
│   ├── naver_datalab.py       # KR search trends (official API)
│   └── naver_search_ad.py     # KR monthly search volume (absolute, official)
├── synth/
│   └── hot_keywords.py        # cross-source synthesis → unified ranking
├── agents/
│   └── trend_summarizer.py    # LLM step: rank → human-readable digest → telegram
├── prompts/                   # LLM prompt templates (KR/EN)
├── state/                     # JSON time-series snapshots (gitignored if heavy)
├── scripts/
│   └── optional/                    # self-hosted runner — opt-in upgrade
│       ├── README.md                # when to flip a workflow to self-hosted
│       ├── setup_self_hosted_runner.sh
│       └── check_runner.sh
└── .github/workflows/
    ├── crawl_trending.yml     # cron 90 min (ubuntu-latest)
    ├── crawl_sectors.yml      # cron daily (ubuntu-latest)
    ├── crawl_keyword.yml      # workflow_dispatch (bot-triggered, ubuntu-latest)
    └── digest.yml             # daily 07 KST + on-demand (ubuntu-latest)
```

## Runner choice — cloud-primary

All 4 workflows use `runs-on: ubuntu-latest`. Rationale:

- meme_project's data sources are mostly **official APIs** (YouTube Data API
  v3, Naver Open API, Naver Search Ad, Telegram Bot API) which work fine
  from cloud IPs — no scraping of user-facing pages that would trip cloud
  IP blocks.
- Cloud GHA gives reproducible fresh-VM runs; no persistent runner to
  maintain, no `loginctl` linger to remember.
- Monthly budget fits well under the 2,000-min private-repo quota (~945
  min/month at the configured cron cadence, ~47% of limit).
- Sibling project [`youtube-to-obsidian`](https://github.com/Sweet-Butters/youtube-to-obsidian)
  uses self-hosted + cloud-fallback because `youtube-transcript-api` is
  reliably blocked on cloud IPs. We don't have that dependency, so we don't
  need that complexity.

If `crawl_keyword.yml` (yt-dlp search) starts getting 403'd often, OR you
want cron faster than 15 min, see [`scripts/optional/README.md`](scripts/optional/README.md)
to flip just that workflow to a self-hosted runner.

## Why each platform (data YouTube doesn't expose)

- **TikTok Creative Center**: trending hashtag growth %, sound trends,
  industry vertical hot content — TikTok signals often lead YouTube Shorts
  by days.
- **Naver Search Ad API**: actual monthly search volume (absolute, not 0-100
  relative) for KR keywords — only free source of true KR search demand.
- **pytrends + YouTube property**: relative YouTube search interest across
  time/region/category.
- **Naver DataLab**: KR search trend curves with demographic breakdowns.

## Stack pinning

Pin the framework explicitly so a breaking change upstream doesn't surprise
this project:

```
# requirements.txt
auto_project @ git+https://github.com/Sweet-Butters/auto_project@v0.3.0
```

Bump the tag deliberately when you want new features. The public framework
follows semver — see its CLAUDE.md.

## Conventions

- Crawler modules must be **idempotent** and **dump JSON to `state/`** with
  ISO-8601 timestamp filenames; agents read state, never re-crawl.
- Secrets and personal identifiers (account emails, project IDs) live in
  GitHub Actions secrets or local `.env`. Never in code, never in commits.
- LLM calls go through `auto_project.llm.call()` for free-tier fallback.
- Alerts go through `auto_project.notify.telegram()`.

## Tests

(not yet) — once a crawler stabilizes, add a smoke test under `tests/`
that mocks the upstream API and asserts the JSON shape written to state.
