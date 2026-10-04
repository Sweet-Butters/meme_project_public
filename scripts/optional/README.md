# Optional: self-hosted GitHub Actions runner

meme_project runs on **cloud GHA (`ubuntu-latest`) by default**. The scripts
here are an opt-in upgrade path — use them only if one of the following
happens:

1. **`crawl_keyword.yml` (yt-dlp search) starts getting 403'd often**. yt-dlp
   interacts with YouTube's user-facing site and can be rate-limited / IP-
   blocked. The official Data API used by `crawl_trending.yml` is unaffected,
   so the upgrade only needs to apply to `crawl_keyword.yml`.

2. **You want sub-15-minute cron** that would otherwise exceed the GHA
   private-repo 2,000 min/month quota.

3. **pytrends rate-limit becomes intolerable** even with retries — though
   note residential IPs face the same rate limits as cloud IPs (Google
   throttles per IP regardless of provenance).

## Files

- `setup_self_hosted_runner.sh` — one-shot installer. Registers your WSL
  as a runner, sets up a systemd user service, prints follow-up steps.
- `check_runner.sh` — quick health check (systemd active? GitHub sees it
  online? recent journal lines).

## Usage

```bash
bash scripts/optional/setup_self_hosted_runner.sh
sudo loginctl enable-linger $USER   # one-time, persists across reboots
bash scripts/optional/check_runner.sh
```

Then edit the workflow you want migrated and change:

```yaml
jobs:
  <job-name>:
    runs-on: ubuntu-latest    # ← change to: self-hosted
```

If you also want per-run dependency isolation on the runner (so a corrupt
venv doesn't poison subsequent runs), swap the `actions/setup-python@v5` +
`pip install` steps for a fresh-venv pattern:

```yaml
- name: Setup per-run venv
  run: |
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip --quiet
    .venv/bin/pip install -r requirements.txt --quiet
# then call .venv/bin/python in subsequent steps
```

## Rollback

```bash
# Stop the service
systemctl --user stop gh-runner-meme.service
systemctl --user disable gh-runner-meme.service

# De-register from GitHub
cd ~/actions-runner/meme_project
./config.sh remove --token "$(gh api -X POST repos/Sweet-Butters/meme_project/actions/runners/remove-token -q .token)"
```

## Why not on by default

See the v0.3 design discussion in commit history. Short version: meme_project
uses mostly **official APIs** (YouTube Data API v3, Naver Open API, Naver
Search Ad, Telegram Bot API) which work fine from cloud IPs. The hybrid
self-hosted + cloud-fallback pattern used by sibling projects
(`youtube-to-obsidian`) doubles the operational surface (two workflow files
per job, two pip-install paths, WebShare proxy management) — that complexity
is justified there because their core dependency (`youtube-transcript-api`)
is reliably IP-blocked on cloud. It's not justified here.
