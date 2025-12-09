#!/usr/bin/env python3
import os
import re
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from googletrans import Translator

# 元の Bluesky RSS（psyarxivbot）
BLUESKY_RSS = "https://bsky.app/profile/psyarxivbot.bsky.social/rss"

# Google翻訳クライアント
translator = Translator(service_urls=["translate.googleapis.com"])


def fetch_bluesky_feed():
    """BlueskyのRSSを取得して BeautifulSoup(xml) にする。"""
    resp = requests.get(BLUESKY_RSS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.content, "xml")


def extract_psyarxiv_url(text):
    """テキスト中から PsyArXiv の URL を1つ拾う。見つからなければ None。"""
    if not text:
        return None
    m = re.search(r"https?://psyarxiv\.com/\S+", text)
    if not m:
        return None
    # 文末の ),. ] みたいなのを落とす
    url = m.group(0).rstrip(").,]")
    return url


def fetch_psyarxiv_title(url):
    """PsyArXiv ページから英語タイトルを取得（og:title 優先）"""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except Exception:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # <meta property="og:title" content="...">
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return og["content"].strip()

    # フォールバック：<title>タグ
    if soup.title and soup.title.string:
        return soup.title.string.strip()

    return None


def ja_title_from_en(en_title):
    """英語タイトルを日本語に翻訳（失敗したらそのまま返す）。"""
    try:
        res = translator.translate(en_title, src="en", dest="ja")
        ja = res.text.strip()
        return ja
    except Exception:
        return en_title


def build_feed():
    """Bluesky→PsyArXiv→日本語タイトル付きRSSを生成して docs/feed.xml に保存。"""
    bs_feed = fetch_bluesky_feed()

    fg = FeedGenerator()
    fg.id("psyarxivbot-ja-feed")
    fg.title("PsyArXiv bot (日本語タイトル付き)")
    fg.description("PsyArXiv bot のポストを日本語タイトル付きで配信する非公式RSSフィード")
    fg.link(href="https://bsky.app/profile/psyarxivbot.bsky.social", rel="alternate")
    # 後でGitHub PagesのURLに差し替え可（とりあえずダミーでOK）
    fg.link(
        href="https://example.github.io/psyarxiv-bluesky-rss-ja/feed.xml",
        rel="self",
    )
    fg.language("ja")

    for item in bs_feed.find_all("item"):
        raw_title = (item.title.string or "").strip() if item.title else ""
        description = (item.description.string or "").strip() if item.description else ""
        text = raw_title or description

        psy_url = extract_psyarxiv_url(text)
        if not psy_url:
            # PsyArXivリンクが無いポストはスキップ
            continue

        en_title = fetch_psyarxiv_title(psy_url) or text
        ja_title = ja_title_from_en(en_title)

        full_title = f"{ja_title} ({en_title})"

        fe = fg.add_entry()
        fe.id(psy_url)
        fe.link(href=psy_url)  # 直接 PsyArXiv に飛ぶ
        fe.title(full_title)

        # Bluesky側のpubDateがあれば使う
        if item.pubDate and item.pubDate.string:
            fe.pubDate(item.pubDate.string)
        else:
            fe.pubDate(datetime.now(timezone.utc))

        # 本文はここでは特に載せない（PsyArXiv側で読む運用）
        fe.description(f"PsyArXiv: {psy_url}")

    # GitHub Pages 用に docs/ 配下にRSSを書き出し
    docs_dir = "docs"
    os.makedirs(docs_dir, exist_ok=True)
    fg.rss_str(pretty=True)
    fg.rss_file(os.path.join(docs_dir, "feed.xml"))


if __name__ == "__main__":
    try:
        build_feed()
    except Exception as e:
        print("Error:", e, file=sys.stderr)
        sys.exit(1)
