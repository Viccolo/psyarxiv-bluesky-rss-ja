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

MODEL = "gpt-5-mini"  # ←使いたいモデル

client = OpenAI()


# =========================
# Bluesky -> URL & Title
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
    m = re.search(r"osf\.io/([a-z0-9]+)", url, flags=re.IGNORECASE)
    return m.group(1) if m else None


def extract_en_title(text: str, url: str) -> str:
    t = text.replace(url, "").strip()
    t = re.sub(r"[:\-–—\s]+$", "", t).strip()
    return t if t else url


# =========================
# OSF metadata (authors)
# =========================

def fetch_osf_preprint_meta(osf_id: str) -> dict | None:
    """
    Fetch OSF preprint metadata. Works for PsyArXiv/OSF preprints.
    """
    api_url = f"https://api.osf.io/v2/preprints/{osf_id}/"
    try:
        r = requests.get(api_url, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def fetch_osf_contributors(osf_id: str) -> list[str]:
    """
    Fetch contributors' full names. Best-effort.
    """
    # Relationship endpoint
    rel_url = f"https://api.osf.io/v2/preprints/{osf_id}/contributors/"
    try:
        r = requests.get(rel_url, timeout=30)
        if r.status_code != 200:
            return []
        data = r.json()
        names = []
        for item in data.get("data", []):
            attrs = item.get("attributes", {})
            nm = attrs.get("full_name")
            if nm:
                names.append(nm)
        return names
    except Exception:
        return []


def format_authors_et_al(names: list[str], max_authors: int = 1) -> str:
    """
    Display e.g. "Smith et al." or "Smith, Tanaka et al." if max_authors>1.
    We keep roman letters as-is; do not translate.
    """
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    head = names[:max_authors]
    if max_authors <= 1:
        return f"{head[0]} et al."
    return f"{', '.join(head)} et al."


# =========================
# OpenAI response parsing (robust)
# =========================

def _coerce_to_dict(obj):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    for fn in ("model_dump", "dict"):
        if hasattr(obj, fn):
            try:
                return getattr(obj, fn)()
            except Exception:
                pass
    if hasattr(obj, "json"):
        try:
            return json.loads(obj.json())
        except Exception:
            pass
    return None


def get_text_from_response(response) -> str:
    try:
        ot = getattr(response, "output_text", None)
        if isinstance(ot, str) and ot.strip():
            return ot.strip()
    except Exception:
        pass

    rd = _coerce_to_dict(response)
    if not rd:
        return ""

    output = rd.get("output", [])
    if not isinstance(output, list):
        return ""

    chunks: list[str] = []
    for msg in output:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "output_text" and isinstance(c.get("text"), str):
                chunks.append(c["text"])
            elif isinstance(c.get("text"), str):
                chunks.append(c["text"])

    return "\n".join([s for s in chunks if s and s.strip()]).strip()


# =========================
# Translation
# =========================

def ja_title_from_en(en_title: str) -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        return en_title

    try:
        response = client.responses.create(
            model=MODEL,
            input=[
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

        ja = get_text_from_response(response).strip()
        if not ja or ja.strip() == en_title.strip():
            return en_title
        ja = re.sub(r"\s+", " ", ja).strip()
        return ja

    except Exception as e:
        print(f"Translation error: {e}", file=sys.stderr)
        return en_title


# =========================
# Entry building
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

        en_title = extract_en_title(text, url)
        ja_title = ja_title_from_en(en_title)

        # Authors from OSF (best-effort; only for osf.io links)
        authors_display = ""
        osf_id = extract_osf_id(url)
        if osf_id:
            names = fetch_osf_contributors(osf_id)
            authors_display = format_authors_et_al(names, max_authors=1)

        # RSS title: Japanese only (short)
        title = ja_title

        # RSS description: English title + authors (and link)
        parts = []
        parts.append(f"EN: {en_title}")
        if authors_display:
            parts.append(f"Authors: {authors_display}")
        parts.append(f"Link: {url}")
        description = " | ".join(parts)

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
    try:
        entries = build_real_entries()
        if entries:
            return entries
        print("No entries from Bluesky feed; falling back to test entries", file=sys.stderr)
    except Exception as e:
        print(f"Unexpected error building entries: {e}", file=sys.stderr)

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
