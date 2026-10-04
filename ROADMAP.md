# meme_project — roadmap

Living document. Tracks what's shipped, what's accumulating, what's next.

## Shipped (2026-05)

- **Crawlers**: YouTube trending, TikTok Creative Center, Naver DataLab,
  Naver Search Ad (opt-in), pytrends sector, **demographic** breakdown
  (gender × age × device per keyword, 11 API calls / kw / snapshot).
- **Synth**: cross-source min-max weighted normalisation; momentum
  (z-score / velocity / acceleration with MIN_STD floor); confidence
  via Reciprocal Rank Fusion (k=60); 5-label classifier (🔥 / 📈 / 💤 /
  📉 / 🆕).
- **Backfill**: one-shot historical re-fetch via pytrends + DataLab APIs
  for any past date range, idempotent. WebShare 10-IP rotation to dodge
  cloud-IP blocks (`WEBSHARE_HOSTS` comma-separated list).
- **Brief**: agents/trend_brief composes a Korean marketing brief with
  Gemini (free tier) → Telegram + commits to `state/trend_brief/` for
  archival.
- **Sync to public**: weekly orphan-init + force-push to
  `meme_project_public`. PII guard uses character-class regex to avoid
  self-match. `.github/` excluded due to PAT scope limits.

## Accumulating

| Metric | Target | Useful when |
|---|---|---|
| Days of synth_hot_keywords history | currently ~38 (backfilled + ongoing) | already useful for momentum/leadlag |
| Daily trend_brief snapshots | ~1 day | "N일 연속 등장" chip needs 7+ days to differentiate |
| Demographic snapshots | 1 snapshot (rolling window inside it) | 4-window comparison gets sharper as the rolling window grows |
| Search Ad API absolute volumes | not enabled | only when user registers a Naver advertiser account |

## Next up (no particular order)

### Data
- [ ] Expand crawler keyword list beyond `AI / 딥러닝 / ChatGPT` —
  parametrise so adding a new keyword is a one-line config change.
- [ ] Add a YouTube `view_count`-aware aggregator per keyword (the
  current synth weights this only via title token matching).
- [ ] Reddit + Twitter/X integration — needs free-tier API key
  evaluation.
- [ ] Search Ad API enablement if user ever registers as advertiser
  (workflow already opt-in).

### Quality / observability
- [ ] Anomaly detection on top of momentum — STL decomposition to
  flag week-of-year-aware outliers, not just z-score against last 7
  days.
- [ ] Backfill resume from partial failure (currently re-runs the
  whole chunk on any error).
- [ ] Per-source rate-limit alerts via the existing Telegram bot.

### Brief
- [ ] Multi-shot LLM brief — first pass categorises, second pass
  drafts narrative with per-category demographic context.
- [ ] Pin a `lead/lag` annotation when the matrix has a strong signal
  (e.g. "Naver leads Google by 7d (ρ=0.68)" line in the brief).
- [ ] Track "brief streak" inside the brief itself (per-keyword "이
  키워드는 N일 연속" badge in the LLM output).

## Won't do (for now)

- Self-hosted GHA runner — current cloud-primary setup works; WebShare
  rotation handles the one cloud-IP-sensitive path (pytrends).
- Real-time push (websocket / SSE) — daily cadence is fine for trend
  data; cost of always-on infra > marginal freshness.

## See also

- `BACKUP_CREDENTIALS.md` — live secret inventory (private only).
- `CLAUDE.md` — codebase conventions.
- Companion repo: `Sweet-Butters/websites` (the dashboard reading this
  state).
