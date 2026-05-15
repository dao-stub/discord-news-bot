"""Fetch RSS feeds and post a daily brief to Discord webhook.

Reads DISCORD_WEBHOOK_NEWS from environment. Fetches all configured feeds
in parallel, filters to items from the last 24 hours, deduplicates, and
posts as Discord embed messages (one per category) to the #news channel
for manual summarization by the curator.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import feedparser
import requests
from dateutil import parser as date_parser

# --- Configuration -----------------------------------------------------------

LOOKBACK_HOURS = 24
PER_CATEGORY_LIMIT = 8           # max items per category in the Discord post
MAX_ITEMS_PER_SOURCE = 2         # no single source can dominate; forces diversity
HTTP_TIMEOUT_SECONDS = 15
USER_AGENT = "cybersec-news-bot/0.2 (https://github.com/your-org/discord-news-bot)"

# Case-insensitive substring matches in title — items matching these are dropped as noise.
# These are admin / promotional patterns that show up in feeds but aren't security content.
TITLE_DENYLIST_PATTERNS: list[str] = [
    "speaking engagement",
    "upcoming event",
    "upcoming talk",
    "newsletter",
    "subscribe to",
    "webinar:",
    "sponsored",
    "register now",
    "join us at",
    "weekly recap",
    "monthly recap",
    "year in review",
]

# Each entry: (category, source_name, feed_url, authority_tier)
# Authority tiers used for ranking when we have too many items.
# CISA Alerts (general advisories feed) is excluded — it's mostly ICS firehose;
# we rely on CISA KEV for the actually-curated high-signal alerts.
FEEDS: list[tuple[str, str, str, int]] = [
    # --- CYBERSEC ---
    ("cybersec", "CISA KEV",            "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.xml", 3),
    ("cybersec", "Project Zero",        "https://googleprojectzero.blogspot.com/feeds/posts/default", 3),
    ("cybersec", "Krebs on Security",   "https://krebsonsecurity.com/feed/", 3),
    ("cybersec", "Schneier on Security","https://www.schneier.com/feed/atom/", 3),
    ("cybersec", "PortSwigger Research","https://portswigger.net/research/rss", 3),
    ("cybersec", "Talos Intelligence",  "https://blog.talosintelligence.com/feeds/posts/default", 3),
    ("cybersec", "The Hacker News",     "https://feeds.feedburner.com/TheHackersNews", 2),
    ("cybersec", "Bleeping Computer",   "https://www.bleepingcomputer.com/feed/", 2),
    ("cybersec", "SecurityWeek",        "https://www.securityweek.com/feed/", 2),
    ("cybersec", "Dark Reading",        "https://www.darkreading.com/rss.xml", 2),
    ("cybersec", "Rapid7 Blog",         "https://www.rapid7.com/blog/rss/", 2),
    # --- AI ---
    ("ai", "Anthropic",                 "https://www.anthropic.com/news/rss.xml", 3),
    ("ai", "OpenAI",                    "https://openai.com/blog/rss.xml", 3),
    ("ai", "Google DeepMind",           "https://deepmind.google/blog/rss.xml", 3),
    ("ai", "Simon Willison",            "https://simonwillison.net/atom/everything/", 3),
    ("ai", "Hugging Face",              "https://huggingface.co/blog/feed.xml", 2),
    ("ai", "The Decoder",               "https://the-decoder.com/feed/", 2),
    ("ai", "Ars Technica AI",           "https://arstechnica.com/tag/artificial-intelligence/feed/", 2),
]

CATEGORY_DISPLAY = {
    "cybersec": ("🛡️  Cybersec Daily", 0x00FF66),  # green accent
    "ai": ("🤖  AI Daily", 0xFFB000),                # amber accent
}


# --- Data --------------------------------------------------------------------


@dataclass
class Item:
    category: str
    source: str
    title: str
    link: str
    published: datetime
    summary: str
    authority: int

    @property
    def fingerprint(self) -> str:
        # Title + link normalized — dedupe across mirror feeds.
        norm = (self.title.strip().lower() + "|" + self.link.split("?", 1)[0]).encode("utf-8")
        return hashlib.sha1(norm).hexdigest()


# --- Fetching ----------------------------------------------------------------


def fetch_one(category: str, source: str, url: str, authority: int) -> list[Item]:
    """Fetch a single feed. Returns [] on any error (logged to stderr)."""
    try:
        resp = requests.get(
            url,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"},
        )
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"[warn] {source}: {type(e).__name__}: {e}", file=sys.stderr)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    out: list[Item] = []
    for entry in parsed.entries:
        pub_raw = entry.get("published") or entry.get("updated") or entry.get("pubDate")
        if not pub_raw:
            continue
        try:
            pub = date_parser.parse(pub_raw)
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if pub < cutoff:
            continue

        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue

        # Skip noise patterns (admin/promotional posts that aren't actual content).
        title_lower = title.lower()
        if any(pat in title_lower for pat in TITLE_DENYLIST_PATTERNS):
            continue

        summary_raw = entry.get("summary") or entry.get("description") or ""
        # Strip HTML tags crudely; keep it short.
        import re
        summary_clean = re.sub(r"<[^>]+>", "", summary_raw).strip()
        if len(summary_clean) > 220:
            summary_clean = summary_clean[:217].rsplit(" ", 1)[0] + "..."

        out.append(Item(
            category=category,
            source=source,
            title=title,
            link=link,
            published=pub,
            summary=summary_clean,
            authority=authority,
        ))
    return out


def fetch_all() -> list[Item]:
    items: list[Item] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_one, cat, src, url, auth) for cat, src, url, auth in FEEDS]
        for f in concurrent.futures.as_completed(futures):
            items.extend(f.result())
    return items


def dedupe_and_rank(items: Iterable[Item]) -> list[Item]:
    seen: dict[str, Item] = {}
    for it in items:
        existing = seen.get(it.fingerprint)
        if existing is None or it.authority > existing.authority:
            seen[it.fingerprint] = it
    # Sort by authority desc, then published desc.
    ranked = sorted(seen.values(), key=lambda x: (-x.authority, -x.published.timestamp()))

    # Cap items per source so a single firehose feed can't dominate the output.
    per_source_counts: dict[str, int] = {}
    diverse: list[Item] = []
    for it in ranked:
        key = f"{it.category}|{it.source}"
        if per_source_counts.get(key, 0) >= MAX_ITEMS_PER_SOURCE:
            continue
        per_source_counts[key] = per_source_counts.get(key, 0) + 1
        diverse.append(it)
    return diverse


# --- Posting -----------------------------------------------------------------


def build_embed(category: str, items: list[Item]) -> dict:
    title, color = CATEGORY_DISPLAY[category]
    lines: list[str] = []
    for it in items[:PER_CATEGORY_LIMIT]:
        # Discord embed description has a 4096-char limit. Keep each line tight.
        line = f"**[{it.title}]({it.link})**\n*{it.source}* — {it.summary}\n"
        # Truncate if we're about to overrun the embed description limit.
        if sum(len(x) for x in lines) + len(line) > 3800:
            break
        lines.append(line)

    description = "\n".join(lines) if lines else "_No new items in the last 24 hours._"
    return {
        "title": title,
        "description": description,
        "color": color,
        "footer": {"text": f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"},
    }


def post_to_webhook(webhook_url: str, embeds: list[dict]) -> None:
    payload = {
        "username": "news-bot",
        "embeds": embeds,
    }
    resp = requests.post(webhook_url, json=payload, timeout=HTTP_TIMEOUT_SECONDS)
    if resp.status_code >= 300:
        raise RuntimeError(f"Discord webhook returned {resp.status_code}: {resp.text[:300]}")


# --- Main --------------------------------------------------------------------


def is_dry_run() -> bool:
    return os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")


def print_dry_run_preview(embeds: list[dict]) -> None:
    """Show what would be posted, formatted for human reading in Actions log."""
    print()
    print("=" * 70)
    print(f"DRY RUN — would post {len(embeds)} embed(s) to webhook (no actual post made)")
    print("=" * 70)
    for i, embed in enumerate(embeds, 1):
        color_hex = f"#{embed.get('color', 0):06X}"
        print()
        print(f"[EMBED {i}] {embed['title']}  (color {color_hex})")
        print("-" * 70)
        print(embed["description"])
        print()
        footer = embed.get("footer", {}).get("text", "")
        if footer:
            print(f"-- {footer}")
    print()
    print("=" * 70)
    print("DRY RUN complete. No Discord post made.")
    print("=" * 70)


def main() -> int:
    dry_run = is_dry_run()
    webhook_url = os.environ.get("DISCORD_WEBHOOK_NEWS")
    if not webhook_url and not dry_run:
        print("[error] DISCORD_WEBHOOK_NEWS env var not set", file=sys.stderr)
        return 2

    items = fetch_all()
    if not items:
        print("[info] No items fetched at all — nothing to post.")
        return 0
    ranked = dedupe_and_rank(items)
    print(f"[info] Fetched {len(items)} items; {len(ranked)} after dedupe + per-source cap.")

    by_category: dict[str, list[Item]] = {"cybersec": [], "ai": []}
    for it in ranked:
        by_category.setdefault(it.category, []).append(it)

    embeds = []
    for cat in ("cybersec", "ai"):
        if by_category.get(cat):
            embeds.append(build_embed(cat, by_category[cat]))
        else:
            print(f"[info] No items for category '{cat}' today.")

    if not embeds:
        print("[info] Nothing to post (all categories empty).")
        return 0

    if dry_run:
        print_dry_run_preview(embeds)
        return 0

    # Discord allows up to 10 embeds per message — we have 2.
    post_to_webhook(webhook_url, embeds)
    print("[ok] Posted daily brief to #news.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
