# discord-news-bot

Daily cybersec + AI news brief poster for our Discord. Runs on GitHub Actions (free), no hosting required.

## What it does

Once a day at 07:00 UTC, the GitHub Actions workflow runs `scripts/fetch_news.py`, which:

1. Fetches ~20 RSS feeds (cybersec authority sources + AI labs + tech press)
2. Filters to items published in the last 24 hours
3. Deduplicates across mirror feeds
4. Ranks by source authority and recency
5. Posts two embed messages (one for cybersec, one for AI) to the `#news` channel for the curator to read and summarize

The curator (Joe initially, then whoever holds the editor role) reads `#news`, runs the items through their own LLM for summarization, and posts the polished result to `#daily-feed` using `scripts/post_summary.py` — which sends it through the same bot identity for branding consistency.

## Setup

### 1. Create the two webhooks in Discord

For each of `#news` and `#daily-feed`:

1. Right-click the channel → **Edit Channel** → **Integrations** → **Webhooks** → **New Webhook**
2. Name it `news-bot-rss` (for `#news`) or `news-bot-publish` (for `#daily-feed`)
3. Copy the webhook URL

**Do not paste either webhook URL anywhere except into GitHub repo secrets.** They are credentials.

### 2. Add the webhooks as GitHub repo secrets

In this repo's **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Value |
|---|---|
| `DISCORD_WEBHOOK_NEWS` | webhook URL for `#news` |
| `DISCORD_WEBHOOK_DAILY_FEED` | webhook URL for `#daily-feed` |

Once saved, even repo admins cannot read these back.

### 3. Verify the workflow runs

Go to the **Actions** tab → **Daily News Brief** → **Run workflow** (manual trigger). Check the run output. On success, an embed should appear in `#news` within ~30 seconds.

If you don't see it: check the Actions run logs. Common issues:

- One or more feeds returned 4xx/5xx — the script logs these and continues. Not fatal.
- All feeds empty — possible if you run twice in 24h. The script only includes items from the last 24h.
- Webhook secret typo — Discord returns 401.

The workflow is also scheduled to run automatically every day at 07:00 UTC.

## Posting a curated summary to `#daily-feed`

Locally on your machine (one-time setup):

```bash
git clone https://github.com/<org>/discord-news-bot
cd discord-news-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Export the daily-feed webhook locally — do NOT commit this anywhere.
export DISCORD_WEBHOOK_DAILY_FEED='https://discord.com/api/webhooks/...'
```

Then to post:

```bash
# From a markdown file:
python scripts/post_summary.py --file todays_summary.md

# Or from stdin (pipe-friendly):
cat todays_summary.md | python scripts/post_summary.py

# With a custom title:
python scripts/post_summary.py --file todays_summary.md \
  --title "Daily Brief — 2026-05-12 (Patch Tuesday edition)"

# Plain text (no embed box), max 2000 chars:
echo "Heads up — CVE-2026-xxxxx dropped" | python scripts/post_summary.py --plain
```

Add a shell alias for speed:

```bash
# In ~/.bashrc or ~/.zshrc:
alias post-brief='python ~/code/discord-news-bot/scripts/post_summary.py'

# Then anywhere:
post-brief --file summary.md
```

## Feed list

See `scripts/fetch_news.py` — the `FEEDS` list at the top is the full configuration. To add or remove a feed, edit the list and commit. Each entry is `(category, source_name, feed_url, authority_tier)`. Authority is 1-3; higher = preferred when deduping mirror coverage.

## Op-sec notes

- Webhook URLs are **secrets**. Never commit them; never paste them in chat, tickets, or docs.
- The fetcher uses a clear `User-Agent` identifying the bot — feeds that block unknown UAs will allow it.
- All feeds are fetched over HTTPS where the source supports it.
- If a webhook is ever exposed, rotate immediately: delete the Discord webhook, create a new one, update the GitHub secret.
- Per charter Q24: rotate webhook URLs quarterly even without a known leak.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Actions run shows "401 Unauthorized" from Discord | Webhook URL invalid or revoked. Rotate. |
| Embed missing items from a known-good feed | Check the run logs for `[warn]` from that source. Often a one-off 5xx; will recover. |
| No post at all | All feeds returned no items in the last 24h, or the script errored. Check Actions logs. |
| Embed truncated | Lots of high-quality items today. Adjust `PER_CATEGORY_LIMIT` in `fetch_news.py`. |

## License

MIT (placeholder — confirm at charter ratification).
