#!/usr/bin/env python3
import os
import sys
import re
import html
import json
from datetime import datetime, timezone
from email.utils import format_datetime

import requests
from openai import OpenAI

# =========================
# Settings
# =========================

ACTOR = "psyarxivbot.bsky.social"
LIMIT = 50

SOURCE_API = (
    "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
    f"?actor={ACTOR}&limit={LIMIT}"
)

DOCS_DIR = "docs"
FEED_FILENAME = "feed.xml"

# Use gpt-5-mini as requested (translation via Chat Completions for stability)
MODEL = "gpt-5-mini"

# Author display style
MAX_AUTHORS_IN_DISPLAY = 1  # 1 => "FirstAuthor et al.", 2 => "A, B et al."

client = OpenAI()


# =========================
# Helpers: Bluesky -> URL & (fallback) title
# =========================

def fetch_source_feed_json() -> dict:
    resp = requests.get(SOURCE_API, timeout=30)
    resp.raise_for_status()
    return resp.json()


def extract_osf_url(text: str | None) -> str | None:
    if not text:
        return None

    # Prefer psyarxiv.com
    m = re.search(r"https?://psyarxiv\.com/\S+", text)
    if m:
        return m.group(0).rstrip(").,]")

    # Otherwise osf.io
    m = re.search(r"https?://osf\.io/\S+", text)
    if m:
        return m.group(0).rstrip(").,]")

    return None


def extract_osf_id(url: str) -> str | None:
    # supports https://osf.io/abc12 or https://osf.io/abc12/
    m = re.search(r"osf\.io/([a-z0-9]+)", url, flags=re.IGNORECASE)
    return m.group(1) if m else None


def extract_en_title_from_post(text: str, url: str) -> str:
    """
    Heuristic fallback: remove URL from post text.
    (We will prefer OSF API title when available.)
    """
    t = text.replace(url, "").strip()
    # trim trailing separators
    t = re.sub(r"[:\-–—\s]+$", "", t).strip()
    return t if t else url


# =========================
# OSF API: title + authors (more reliable via include)
# =========================

def fetch_osf_preprint_with_contributors(osf_id: str) -> dict | None:
    """
    Fetch preprint metadata with contributors included.
    """
    url = f"https://api.osf.io/v2/preprints/{osf_id}/?include=contributors"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def get_osf_title_and_authors(osf_id: str) -> tuple[str | None, list[str]]:
    """
    Returns (english_title_or_None, ordered_author_names)
    """
    j = fetch_osf_preprint_with_contributors(osf_id)
    if not j:
        return (None, [])

    # Title
    title = None
    try:
        title = j.get("data", {}).get("attributes", {}).get("title")
    except Exception:
        title = None

    # Author order: relationships.contributors.data gives ids in order (often)
    order_ids: list[str] = []
    try:
        rel = j.get("data", {}).get("relationships", {}).get("contributors", {}).get("data", [])
        if isinstance(rel, list):
            order_ids = [x.get("id") for x in rel if isinstance(x, dict) and x.get("id")]
    except Exception:
        order_ids = []

    # included: id -> full_name
    id2name: dict[str, str] = {}
    included = j.get("included", [])
    if isinstance(included, list):
        for it in included:
            if not isinstance(it, dict):
                continue
            it_id = it.get("id")
            attrs = it.get("attributes", {})
            full = attrs.get("full_name")
            if it_id and isinstance(full, str) and full.strip():
                id2name[it_id] = full.strip()

    # build ordered names
    names: list[str] = []
    for cid in order_ids:
        nm = id2name.get(cid)
        if nm:
            names.append(nm)

    # fallback: if relationships empty, just pull from included (unordered)
    if not names and isinstance(included, list):
        for it in included:
            if not isinstance(it, dict):
                continue
            full = it.get("attributes", {}).get("full_name")
            if isinstance(full, str) and full.strip():
                names.append(full.strip())

    # de-dup while preserving order
    seen = set()
    out = []
    for n in names:
        if n not in seen:
            out.append(n)
            seen.add(n)

    return (title, out)


def format_authors_et_al(names: list[str], max_authors: int = 1) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    head = names[:max_authors] if max_authors > 0 else [names[0]]
    if max_authors <= 1:
        return f"{head[0]} et al."
    return f"{', '.join(head)} et al."


# =========================
# Translation: gpt-5-mini via Chat Completions (stable)
# =========================

def ja_title_from_en(en_title: str) -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        return en_title

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional academic translator. "
                        "Translate paper titles into natural Japanese suitable for academic contexts."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Translate the following academic paper title into natural Japanese.\n"
                        "Rules:\n"
                        "- Output ONLY the Japanese title (one line)\n"
                        "- Do not add quotes, brackets, or explanations\n"
                        "- Keep proper nouns and abbreviations as-is when appropriate\n\n"
                        f"{en_title}"
                    ),
                },
            ],
            temperature=0.2,
        )
        ja = (resp.choices[0].message.content or "").strip()
        if not ja:
            return en_title
        ja = re.sub(r"\s+", " ", ja).strip()
        return ja
    except Exception as e:
        print(f"Translation error: {e}", file=sys.stderr)
        return en_title


# =========================
# Build entries
# =========================

def build_real_entries() -> list[dict]:
    try:
        data = fetch_source_feed_json()
    except Exception as e:
        print(f"Error fetching Bluesky feed: {e}", file=sys.stderr)
        return []

    entries: list[dict] = []

    for item in data.get("feed", []):
        post = item.get("post", {})
        record = post.get("record", {})

        text = record.get("text", "")
        if not text:
            continue

        url = extract_osf_url(text)
        if not url:
            continue

        # Prefer OSF API title & authors when osf.io exists
        en_title = None
        authors_display = ""

        osf_id = extract_osf_id(url)
        if osf_id:
            api_title, author_names = get_osf_title_and_authors(osf_id)
            if isinstance(api_title, str) and api_title.strip():
                en_title = api_title.strip()
            if author_names:
                authors_display = format_authors_et_al(author_names, max_authors=MAX_AUTHORS_IN_DISPLAY)

        # fallback title from post
        if not en_title:
            en_title = extract_en_title_from_post(text, url)

        ja_title = ja_title_from_en(en_title)

        # RSS title: Japanese only
        title = ja_title

        # RSS description: English + authors + link (authors optional)
        parts = [f"EN: {en_title}"]
        if authors_display:
            parts.append(f"Authors: {authors_display}")
        parts.append(f"Link: {url}")
        description = " | ".join(parts)

        # pubDate from createdAt if available, else now
        created_at = record.get("createdAt")
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                pub_date = format_datetime(dt.astimezone(timezone.utc))
            except Exception:
                pub_date = format_datetime(datetime.now(timezone.utc))
        else:
            pub_date = format_datetime(datetime.now(timezone.utc))

        entries.append(
            {
                "title": title,
                "description": description,
                "link": url,
                "guid": f"{url}#ja",
                "pubDate": pub_date,
            }
        )

    return entries


def build_test_entries() -> list[dict]:
    now = format_datetime(datetime.now(timezone.utc))
    return [
        {
            "title": "テスト論文その1",
            "description": "EN: Test paper one | Authors: Smith et al. | Link: https://psyarxiv.com/abcd1",
            "link": "https://psyarxiv.com/abcd1",
            "guid": "https://psyarxiv.com/abcd1#ja",
            "pubDate": now,
        },
        {
            "title": "テスト論文その2",
            "description": "EN: Test paper two | Authors: Tanaka et al. | Link: https://psyarxiv.com/abcd2",
            "link": "https://psyarxiv.com/abcd2",
            "guid": "https://psyarxiv.com/abcd2#ja",
            "pubDate": now,
        },
    ]


def build_entries() -> list[dict]:
    entries = build_real_entries()
    if entries:
        return entries
    print("No entries from Bluesky feed; falling back to test entries", file=sys.stderr)
    return build_test_entries()


# =========================
# RSS generation
# =========================

def build_rss_xml(entries: list[dict]) -> str:
    channel_title = "PsyArXiv bot (日本語タイトル付き)"
    channel_link = f"https://bsky.app/profile/{ACTOR}"
    channel_description = (
        f"{ACTOR} のポストから PsyArXiv 論文へのリンクを集め、"
        "日本語タイトル（英語タイトル）形式で配信する非公式RSSフィード"
    )

    items_xml = []
    for e in entries:
        title_esc = html.escape(e["title"])
        link_esc = html.escape(e["link"])
        guid_esc = html.escape(e.get("guid") or e["link"])
        pub_date = e["pubDate"]
        desc_esc = html.escape(e.get("description", ""))

        items_xml.append(
            f"""  <item>
    <title>{title_esc}</title>
    <link>{link_esc}</link>
    <guid isPermaLink="false">{guid_esc}</guid>
    <pubDate>{pub_date}</pubDate>
    <description>{desc_esc}</description>
  </item>"""
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>{html.escape(channel_title)}</title>
  <link>{html.escape(channel_link)}</link>
  <description>{html.escape(channel_description)}</description>
  <language>ja</language>
{chr(10).join(items_xml)}
</channel>
</rss>
"""


def main():
    entries = build_entries()
    rss_xml = build_rss_xml(entries)

    os.makedirs(DOCS_DIR, exist_ok=True)
    out_path = os.path.join(DOCS_DIR, FEED_FILENAME)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(rss_xml)

    print(f"Wrote {out_path} ({len(entries)} items)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Fatal error:", e, file=sys.stderr)
        sys.exit(1)
