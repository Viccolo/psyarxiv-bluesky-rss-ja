#!/usr/bin/env python3
import os
import sys
import re
import html
from datetime import datetime, timezone
from email.utils import format_datetime

import requests
from bs4 import BeautifulSoup
from datetime import datetime
from feedgen.feed import FeedGenerator
from openai import OpenAI

# =========================
# Settings
# 設定
# =========================

ACTOR = "psyarxivbot.bsky.social"
LIMIT = 50

BLUESKY_API = (
    "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
    f"?actor={ACTOR}&limit={LIMIT}"
)
BLUESKY_FEED_URL = "https://bsky.app/profile/psyarxivbot.bsky.social"
OUTPUT_PATH = "docs/feed.xml"

DOCS_DIR = "docs"
FEED_FILE = "feed.xml"
# 著者表示数（ここを変えるだけ）
AUTHOR_DISPLAY_LIMIT = 3   # 例：3 → 3人まで表示、それ以上は et al.

# 安定重視
MODEL = "gpt-4.1-mini"
MODEL_TRANSLATE = "gpt-4.1-mini"  # 安定動作確認済み

# 著者表示：FirstAuthor et al.
MAX_AUTHORS = 1
client = OpenAI()  # OPENAI_API_KEY を使用

client = OpenAI()

# =========================
# Bluesky helpers
# ユーティリティ
# =========================

def fetch_bluesky_feed() -> dict:
    r = requests.get(BLUESKY_API, timeout=30)
    r.raise_for_status()
    return r.json()


def extract_osf_url(text: str | None) -> str | None:
    if not text:
        return None

    m = re.search(r"https?://psyarxiv\.com/\S+", text)
    if m:
        return m.group(0).rstrip(").,]")

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
# OSF API helpers
# =========================

def fetch_preprint(osf_id: str) -> dict | None:
    url = f"https://api.osf.io/v2/preprints/{osf_id}/"
def fetch_html(url: str) -> BeautifulSoup | None:
try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
except Exception:
return None


def get_node_id_from_preprint(osf_id: str) -> str | None:
    j = fetch_preprint(osf_id)
    if not j:
        return None
    return (
        j.get("data", {})
         .get("relationships", {})
         .get("node", {})
         .get("data", {})
         .get("id")
    )


def fetch_node_authors(node_id: str) -> list[str]:
    url = f"https://api.osf.io/v2/nodes/{node_id}/contributors/"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            return []
        j = r.json()

        names = []
        for it in j.get("data", []):
            full = it.get("attributes", {}).get("full_name")
            if isinstance(full, str) and full.strip():
                names.append(full.strip())

        # deduplicate (preserve order)
        seen = set()
        out = []
        for n in names:
            if n not in seen:
                out.append(n)
                seen.add(n)
        return out

    except Exception:
def fetch_authors(osf_url: str) -> list[str]:
    """
    PsyArXiv 論文ページの Authors セクションから
    表示されている名前をそのまま取得（厳密性は追わない）
    """
    soup = fetch_html(osf_url)
    if soup is None:
return []

    authors = []

def get_preprint_title(osf_id: str) -> str | None:
    j = fetch_preprint(osf_id)
    if not j:
        return None
    return j.get("data", {}).get("attributes", {}).get("title")
    # 2025年以降のUI対応：profileリンクを持つ a タグ
    for a in soup.select('a[href^="/profile/"]'):
        name = a.get_text(strip=True)
        if name:
            authors.append(name)

    # 重複除去（順序保持）
    return list(dict.fromkeys(authors))


def format_authors_et_al(names: list[str], max_authors: int = 1) -> str:
    if not names:
def format_authors(authors: list[str], limit: int) -> str:
    """
    表示人数は後で自由に変更できるように分離
    """
    if not authors:
return ""
    if len(names) == 1:
        return names[0]
    if max_authors <= 1:
        return f"{names[0]} et al."
    return f"{', '.join(names[:max_authors])} et al."

    if len(authors) <= limit:
        return ", ".join(authors)

# =========================
# Translation (stable)
# =========================
    return ", ".join(authors[:limit]) + " et al."

def translate_title_to_ja(en_title: str) -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        return en_title

def translate_title(title_en: str) -> str:
    """
    英語タイトル → 日本語タイトル
    """
try:
        resp = client.chat.completions.create(
            model=MODEL,
        res = client.chat.completions.create(
            model=MODEL_TRANSLATE,
messages=[
{
"role": "system",
                    "content": (
                        "You are a professional academic translator. "
                        "Translate academic paper titles into natural Japanese."
                    ),
                    "content": "Translate academic paper titles into natural Japanese."
},
{
"role": "user",
                    "content": (
                        "Translate the following paper title into Japanese.\n"
                        "Rules:\n"
                        "- Output ONLY the Japanese title\n"
                        "- No quotes, no explanations\n\n"
                        f"{en_title}"
                    ),
                },
                    "content": title_en
                }
],
temperature=0.2,
)

        ja = (resp.choices[0].message.content or "").strip()
        return ja if ja else en_title

    except Exception as e:
        print(f"[translate error] {e}", file=sys.stderr)
        return en_title
        return res.choices[0].message.content.strip()
    except Exception:
        return title_en  # 失敗時は英語のまま


# =========================
# Entry builder
# メイン処理
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
        if not osf_id:
            continue

        # English title
        en_title = get_preprint_title(osf_id)
        if not en_title:
            en_title = fallback_title_from_post(text, url)

        # Japanese title
        ja_title = translate_title_to_ja(en_title)

        # Authors (node-based)
        authors = ""
        node_id = get_node_id_from_preprint(osf_id)
        if node_id:
            names = fetch_node_authors(node_id)
            if names:
                authors = format_authors_et_al(names, MAX_AUTHORS)

        # Description: EN + Authors + Link
        desc_parts = [f"EN: {en_title}"]
        if authors:
            desc_parts.append(f"Authors: {authors}")
        desc_parts.append(f"Link: {url}")

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
def build_feed():
    fg = FeedGenerator()
    fg.load_extension("dc", atom=False, rss=True)

    return entries
    fg.title("PsyArXiv bot (日本語タイトル付き)")
    fg.link(href=BLUESKY_FEED_URL)
    fg.description(
        "psyarxivbot.bsky.social のポストから PsyArXiv 論文へのリンクを集め、"
        "日本語タイトル（英語タイトル）＋著者情報を配信する非公式RSSフィード"
    )
    fg.language("ja")

    # ----
    # ここでは「すでに取得済み」と仮定
    # 実際は Bluesky から osf_url / title_en / pub_date を取っているはず
    # ----

# =========================
# RSS writer
# =========================
    papers = fetch_papers_somehow()  # ← 既存処理をそのまま使う想定

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
    for paper in papers:
        title_en = paper["title"]
        osf_url = paper["url"]
        pub_date = paper["published"]

        title_ja = translate_title(title_en)
        authors = fetch_authors(osf_url)
        author_text = format_authors(authors, AUTHOR_DISPLAY_LIMIT)

        fe = fg.add_entry()
        fe.id(osf_url + "#ja")
        fe.link(href=osf_url)
        fe.pubDate(pub_date)

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
        # タイトルは日本語のみ（RSS一覧で視認性優先）
        fe.title(title_ja)

        # description に英語タイトル＋著者
        desc = f"EN: {title_en}"
        if author_text:
            desc += f" | Authors: {author_text}"

def main():
    entries = build_entries()
    rss = build_rss(entries)
        fe.description(desc)

    os.makedirs(DOCS_DIR, exist_ok=True)
    path = os.path.join(DOCS_DIR, FEED_FILE)
    with open(path, "w", encoding="utf-8") as f:
        f.write(rss)
    fg.rss_file(OUTPUT_PATH, encoding="utf-8")

    print(f"Wrote {path} ({len(entries)} items)")

# =========================
# 実行
# =========================

if __name__ == "__main__":
    main()
    build_feed()
