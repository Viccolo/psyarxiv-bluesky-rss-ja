#!/usr/bin/env python3
import os
import re
import json
import time
from datetime import datetime, timezone
from xml.dom import minidom

import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from openai import OpenAI

# =========================
# Settings
# =========================
ACTOR = "psyarxivbot.bsky.social"
BLUESKY_API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"

LIMIT_PER_PAGE = 100
MAX_POSTS_FETCH = 400      # 取得する投稿数（多めに取ってURL抽出）
MAX_ITEMS = 60             # RSSに入れる最大件数
SLEEP_SEC = 0.1            # OSFへの連打を少し抑える

OUTPUT_PATH = "docs/feed.xml"
CACHE_PATH = "docs/cache.json"  # 翻訳キャッシュ（推奨）

MODEL_TRANSLATE = "gpt-4.1-mini"

client = OpenAI()  # OPENAI_API_KEY 必須

URL_RE = re.compile(r"https?://[^\s)>\]]+")


# =========================
# Cache
# =========================
def load_cache():
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_cache(cache: dict):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# =========================
# Bluesky
# =========================
def fetch_author_feed(max_records: int = 300):
    items = []
    cursor = None

    while len(items) < max_records:
        params = {"actor": ACTOR, "limit": LIMIT_PER_PAGE}
        if cursor:
            params["cursor"] = cursor

        r = requests.get(BLUESKY_API, params=params, timeout=30)
        r.raise_for_status()
        j = r.json()

        feed = j.get("feed", []) or []
        items.extend(feed)

        cursor = j.get("cursor")
        if not cursor or not feed:
            break

    return items[:max_records]


def _extract_from_facets(record: dict) -> list[str]:
    urls = []
    for facet in (record.get("facets") or []):
        for feat in (facet.get("features") or []):
            uri = feat.get("uri")
            if isinstance(uri, str) and uri.startswith("http"):
                urls.append(uri)
    return urls

def _extract_from_embed(record: dict) -> list[str]:
    urls = []
    embed = record.get("embed")
    if not isinstance(embed, dict):
        return urls

    ext = embed.get("external")
    if isinstance(ext, dict):
        uri = ext.get("uri")
        if isinstance(uri, str) and uri.startswith("http"):
            urls.append(uri)

    media = embed.get("media")
    if isinstance(media, dict):
        ext2 = media.get("external")
        if isinstance(ext2, dict):
            uri2 = ext2.get("uri")
            if isinstance(uri2, str) and uri2.startswith("http"):
                urls.append(uri2)

    return urls

def extract_urls_from_post(item: dict) -> list[str]:
    post = item.get("post") or {}
    record = post.get("record") or {}
    text = record.get("text") or ""

    urls = []
    urls.extend(_extract_from_facets(record))
    urls.extend(_extract_from_embed(record))
    urls.extend(URL_RE.findall(text))

    cleaned = []
    for u in urls:
        cleaned.append(u.rstrip(").,;]>\u3001\u3002"))

    seen = set()
    out = []
    for u in cleaned:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


def normalize_target_url(url: str) -> str | None:
    """
    RSSは「PsyArXivに直接飛べる方が便利」だったので、
    psyarxiv.com が取れればそれ優先。
    ただし投稿が osf.io しか無い場合もあるので両対応。
    """
    if "psyarxiv.com/" in url:
        m = re.search(r"psyarxiv\.com/([a-z0-9]+)", url, flags=re.I)
        if m:
            return f"https://psyarxiv.com/{m.group(1).lower()}"
        return url
    if "osf.io/" in url:
        m = re.search(r"osf\.io/([a-z0-9]+)", url, flags=re.I)
        if m:
            return f"https://osf.io/{m.group(1).lower()}"
        return url
    return None


# =========================
# Page parse (title only)
# =========================
def get_soup(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception:
        return None

def parse_title_from_page(soup: BeautifulSoup) -> str:
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        t = og["content"].strip()
        if t:
            return t

    h1 = soup.find("h1")
    if h1:
        t = h1.get_text(strip=True)
        if t:
            return t

    ttag = soup.find("title")
    if ttag:
        t = ttag.get_text(strip=True)
        if t:
            return t

    return "Untitled"


# =========================
# Translation
# =========================
def translate_title(title_en: str, cache: dict) -> str:
    if title_en in cache:
        return cache[title_en]

    try:
        res = client.chat.completions.create(
            model=MODEL_TRANSLATE,
            messages=[
                {"role": "system", "content": "Translate academic paper titles into natural Japanese."},
                {"role": "user", "content": "Output ONLY the Japanese title.\n\n" + title_en},
            ],
            temperature=0.2,
        )
        ja = (res.choices[0].message.content or "").strip()
        if not ja:
            ja = title_en
    except Exception:
        ja = title_en

    cache[title_en] = ja
    return ja


# =========================
# XML pretty
# =========================
def write_pretty_xml(path: str, xml_bytes: bytes):
    try:
        dom = minidom.parseString(xml_bytes)
        pretty = dom.toprettyxml(indent="  ", encoding="UTF-8")
        with open(path, "wb") as f:
            f.write(pretty)
    except Exception:
        with open(path, "wb") as f:
            f.write(xml_bytes)


# =========================
# Main
# =========================
def build_feed():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    cache = load_cache()

    feed_items = fetch_author_feed(max_records=MAX_POSTS_FETCH)

    # URL -> newest_date
    url_to_date = {}
    for it in feed_items:
        post = (it.get("post") or {})
        record = (post.get("record") or {})
        created = record.get("createdAt")  # ISO8601

        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.now(timezone.utc)

        for u in extract_urls_from_post(it):
            nu = normalize_target_url(u)
            if not nu:
                continue
            if ("psyarxiv.com/" not in nu) and ("osf.io/" not in nu):
                continue

            prev = url_to_date.get(nu)
            if (prev is None) or (dt > prev):
                url_to_date[nu] = dt

    selected = sorted(url_to_date.items(), key=lambda x: x[1], reverse=True)[:MAX_ITEMS]

    fg = FeedGenerator()
    fg.title("PsyArXiv bot (日本語タイトル付き)")
    fg.link(href=f"https://bsky.app/profile/{ACTOR}")
    fg.description(
        "psyarxivbot.bsky.social のポストから PsyArXiv 論文へのリンクを集め、"
        "日本語タイトル（英語タイトル）形式で配信する非公式RSSフィード"
    )
    fg.language("ja")
    fg.lastBuildDate(datetime.now(timezone.utc))

    for url, dt in selected:
        soup = get_soup(url)
        if soup is None:
            continue

        title_en = parse_title_from_page(soup)
        title_ja = translate_title(title_en, cache)

        fe = fg.add_entry()
        fe.id(url + "#ja")
        fe.link(href=url)
        fe.pubDate(dt.astimezone(timezone.utc))

        # タイトルは「日本語（英語）」で一覧性を上げる（あなたの当初要件）
        fe.title(f"{title_ja} ({title_en})")

        # description は短く（本文は要らない）
        fe.description(f"Link: {url}")

        time.sleep(SLEEP_SEC)

    xml_bytes = fg.rss_str(pretty=False)
    write_pretty_xml(OUTPUT_PATH, xml_bytes)

    save_cache(cache)
    print(f"Wrote {OUTPUT_PATH} with {len(selected)} items")


if __name__ == "__main__":
    build_feed()
