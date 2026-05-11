"""Post a curated summary to the #daily-feed channel via Discord webhook.

Usage (from terminal, after exporting DISCORD_WEBHOOK_DAILY_FEED locally):

    cat summary.md | python scripts/post_summary.py
    python scripts/post_summary.py --file summary.md
    python scripts/post_summary.py --title "Daily Brief — 2026-05-11" --file summary.md

The webhook URL is read from the DISCORD_WEBHOOK_DAILY_FEED environment variable.
Do NOT hardcode the URL in this file or pass it on the command line.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import requests

DEFAULT_COLOR = 0x00FF66  # cybersec green
DEFAULT_USERNAME = "news-bot"
HTTP_TIMEOUT = 15


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Post a summary to #daily-feed via webhook.")
    p.add_argument("--file", "-f", help="Path to markdown file. If omitted, reads from stdin.")
    p.add_argument("--title", "-t", default=None,
                   help="Embed title. Defaults to 'Daily Brief — YYYY-MM-DD' in UTC.")
    p.add_argument("--color", default=str(DEFAULT_COLOR),
                   help="Embed sidebar color (decimal or 0x-hex). Default: 0x00FF66.")
    p.add_argument("--username", default=DEFAULT_USERNAME,
                   help=f"Bot display name on the post. Default: {DEFAULT_USERNAME}.")
    p.add_argument("--plain", action="store_true",
                   help="Post as plain content (no embed). Useful for short posts.")
    return p.parse_args()


def read_content(file_path: str | None) -> str:
    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    if sys.stdin.isatty():
        print("[error] No --file given and stdin is empty. Pipe content or use --file.", file=sys.stderr)
        sys.exit(2)
    return sys.stdin.read().strip()


def parse_color(s: str) -> int:
    s = s.strip()
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s)


def main() -> int:
    args = parse_args()

    webhook = os.environ.get("DISCORD_WEBHOOK_DAILY_FEED")
    if not webhook:
        print("[error] DISCORD_WEBHOOK_DAILY_FEED env var not set. Export it locally first:",
              file=sys.stderr)
        print("        export DISCORD_WEBHOOK_DAILY_FEED='https://discord.com/api/webhooks/...'",
              file=sys.stderr)
        return 2

    content = read_content(args.file)
    if not content:
        print("[error] Empty content; nothing to post.", file=sys.stderr)
        return 2

    payload: dict = {"username": args.username}

    if args.plain:
        # Plain content: Discord allows up to 2000 chars.
        if len(content) > 2000:
            print(f"[error] Plain content is {len(content)} chars; limit is 2000.", file=sys.stderr)
            return 2
        payload["content"] = content
    else:
        # Embed: description max 4096 chars; total embed payload max 6000.
        title = args.title or f"Daily Brief — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        if len(content) > 4000:
            print(f"[warn] Content is {len(content)} chars; trimming to fit embed description (4000).",
                  file=sys.stderr)
            content = content[:3997] + "..."
        payload["embeds"] = [{
            "title": title,
            "description": content,
            "color": parse_color(args.color),
            "footer": {"text": f"Posted {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"},
        }]

    resp = requests.post(webhook, json=payload, timeout=HTTP_TIMEOUT)
    if resp.status_code >= 300:
        print(f"[error] Discord returned {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        return 1

    print(f"[ok] Posted to #daily-feed (status {resp.status_code}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
