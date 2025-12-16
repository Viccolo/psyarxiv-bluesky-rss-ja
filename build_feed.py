#!/usr/bin/env python3
import os
import sys
import re
import html
from datetime import datetime, timezone
from email.utils import format_datetime

import requests
from openai import OpenAI

# =========================
# Basic settings
# =========================

ACTOR = "psyarxivbot.bsky.social"
LIMIT = 50

BLUESKY_API = (
    "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
    f"?actor={ACTOR}&limit={LIMIT}"
)

DOCS_DIR = "docs"
FEED_FILE = "feed.xml"

# ★ 安定重視
MODEL = "gpt-4.1-mini"

# 著者表示：FirstAuthor et al.
MAX_AUTHORS = 1

client = OpenAI()

# =========================
# Bluesky helpers
# =========================

def fetch_bluesky_feed() -> dict:
    r = requests.get(BLUESKY_API, timeout=30)
    r.raise_for_status()
    return r.json()


def extract_osf_url(text: str | None) -> str | None:
    if not text:
        return None

    # Prefer psyarxiv.com
    m = re.search(r"https?://psyarxiv\.com/\S+", text)
    if m:
        return m.group(0).rstrip(").,]")

    # Fallback to osf.io
    m = re.search(r"https?://osf\.io/\S+", text)
    if m:
        return m.group(0).rstrip(").,]")

    return None


def extract_osf_id(url: str) -> str | None:
    m = re.search(r"osf\.io/([a-z0-9]+)", url, flags=re.IGNORECASE)
    return m.group(1) if m else None


def fallback_title_from_post(text: str, url: str) -> str:
    t = text.replace(url, "").strip()
    t = re.sub(r"[:\-–—\s]+$", "", t).strip()
    return t if t else url


# =========================
# OSF API (title + authors)
# =========================

def fetch_osf_metadata(osf_id: str) -> dict | None:
    url = f"https://api.osf.io/v2/preprints/{osf_id}/?include=contributors"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def get_title_and_authors(osf_id: str) -> tuple[str | None, list[str]]:
    j = fetch_osf_metadata(osf_id)
    if not j:
        return None, []

    # English title
    title = j.get("data", {}).get("attributes", {}).get("title")

    # Author order from relationships
    order_ids = []
    rel = j.get("data", {}).get("relationships", {}).get("contributors", {}).get("data", [])
    if isinstance(rel, list):
        order_ids = [x.get("id") for x in rel if isinstance(x, dict) and x.get("id")]

    # included -> id -> full_name
    id2name = {}
    for it in j.get("included", []):
        if not isinstance(it, dict):
            continue
        fid = it.get("id")
        name = it.get("attributes", {}).get("full_name")
        if fid and isinstance(name, str):
            id2name[fid] = name.strip()

    names = [id2name[i] for i in order_ids if i in id2name]

    # fallback if ordering missing
    if not names:
        for it in j.get("included", []):
            name = it.get("attributes", {}).get("full_name")
            if isinstance(name, str):
                names.append(name.strip())

    # deduplicate, preserve order
    seen = set()
    uniq = []
    for n in names:
        if n not in seen:
            uniq.append(n)
            seen.add(n)

    return title, uniq


def format_authors_et_al(names: list[str], max_authors: int = 1) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if max_authors <= 1:
        return f"{names[0]} et al."
    return f"{', '.join(names[:max_authors])} et al."


# =========================
# Translation (stable)
# =========================

def translate_title_to_ja(en_title: str) -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        return en_title

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional academic psychological translator. "
                        "Translate academic paper titles into natural Japanese in psychology."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Translate the following paper title into psychologically valid and professional Japanese.\n"
                        "Rules:\n"
                        "- Output ONLY the Japanese title\n"
                        "- No quotes, no explanations\n\n"
                        f"{en_title}"
                    ),
                },
            ],
            temperature=0.2,
        )

        ja = (resp.choices[0].message.content or "").strip()
        return ja if ja else en_title

    except Exception as e:
        print(f"[translate error] {e}", file=sys.stderr)
        return en_title


# =========================
# Entry builder
# =========================

def build_entries() -> list[dict]:
    data = fetch_bluesky_feed()
    entries = []

    for item in data.get("feed", []):
        post = item.get("post", {})
        record = post.get("record", {})
        text = record.get("text", "")

        url = extract_osf_url(text)
        if not url:
            continue

        osf_id = extract_osf_id(url)

        en_title = None
        authors = ""

        if osf_id:
            api_title, names = get_title_and_authors(osf_id)
            if api_title:
                en_title = api_title
            if names:
                authors = format_authors_et_al(names, MAX_AUTHORS)

        if not en_title:
            en_title = fallback_title_from_post(text, url)

        ja_title = translate_title_to_ja(en_title)

        # description: English + authors + link
        desc_parts = [f"{en_title}"]

        created = record.get("createdAt")
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.now(timezone.utc)

        entries.append(
            {
                "title": ja_title,
                "description": " | ".join(desc_parts),
                "link": url,
                "guid": f"{url}#ja",
                "pubDate": format_datetime(dt.astimezone(timezone.utc)),
            }
        )

    return entries


# =========================
# RSS writer
# =========================

def build_rss(entries: list[dict]) -> str:
    items = []
    for e in entries:
        items.append(
            f"""  <item>
    <title>{html.escape(e["title"])}</title>
    <link>{html.escape(e["link"])}</link>
    <guid isPermaLink="false">{html.escape(e["guid"])}</guid>
    <pubDate>{e["pubDate"]}</pubDate>
    <description>{html.escape(e["description"])}</description>
  </item>"""
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>PsyArXiv bot (日本語タイトル付き)</title>
  <link>https://bsky.app/profile/{ACTOR}</link>
  <description>{ACTOR} のポストから PsyArXiv 論文へのリンクを集め、日本語タイトル付きで配信する非公式RSSフィード</description>
  <language>ja</language>
{chr(10).join(items)}
</channel>
</rss>
"""


def main():
    entries = build_entries()
    rss = build_rss(entries)

    os.makedirs(DOCS_DIR, exist_ok=True)
    path = os.path.join(DOCS_DIR, FEED_FILE)
    with open(path, "w", encoding="utf-8") as f:
        f.write(rss)

    print(f"Wrote {path} ({len(entries)} items)")


if __name__ == "__main__":
    main()
