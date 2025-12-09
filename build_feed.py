#!/usr/bin/env python3
import os
import sys
import html
from datetime import datetime, timezone
from email.utils import format_datetime

import requests
from bs4 import BeautifulSoup
from googletrans import Translator

# ---- 設定 ----
# PsyArXiv 本体の RSS（ここを入力元にします）
SOURCE_RSS = "https://psyarxiv.com/rss"

DOCS_DIR = "docs"
FEED_FILENAME = "feed.xml"

# Google 翻訳クライアント
translator = Translator(service_urls=["translate.googleapis.com"])


def fetch_source_feed_xml():
    """PsyArXiv の RSS を取得して BeautifulSoup(xml) にする。"""
    resp = requests.get(SOURCE_RSS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.content, "xml")


def ja_title_from_en(en_title: str) -> str:
    """英語タイトルを日本語に翻訳（失敗したらそのまま返す）。"""
    try:
        res = translator.translate(en_title, src="en", dest="ja")
        ja = res.text.strip()
        return ja or en_title
    except Exception:
        return en_title


def build_entries():
    """PsyArXiv RSS → 日本語タイトル付きエントリリストを作る。"""
    feed = fetch_source_feed_xml()
    entries = []

    for item in feed.find_all("item"):
        # link
        link_tag = item.find("link")
        if link_tag is None:
            continue

        url = (link_tag.string or "").strip()
        if not url:
            url = (link_tag.get("href") or "").strip()
        if not url:
            continue

        # 英語タイトル
        if item.title and item.title.string:
            en_title = item.title.string.strip()
        else:
            en_title = url

        # 日本語タイトル
        ja_title = ja_title_from_en(en_title)
        full_title = f"{ja_title} ({en_title})"

        # pubDate（なければ現在時刻）
        if item.pubDate and item.pubDate.string:
            pub_date = item.pubDate.string.strip()
        else:
            pub_date = format_datetime(datetime.now(timezone.utc))

        entries.append(
            {
                "title": full_title,
                "link": url,
                "pubDate": pub_date,
            }
        )

    return entries


def build_rss_xml(entries):
    """entries リストからシンプルな RSS 2.0 の XML を生成。"""
    channel_title = "PsyArXiv bot (日本語タイトル付き)"
    channel_link = "https://bsky.app/profile/psyarxivbot.bsky.social"
    channel_description = (
        "psyarxivbot.bsky.social のポストから PsyArXiv 論文へのリンクを集め、"
        "日本語タイトル（英語タイトル）形式で配信する非公式RSSフィード"
    )

    items_xml = []
    for e in entries:
        title_esc = html.escape(e["title"])
        link_esc = html.escape(e["link"])
        guid_esc = link_esc
        pub_date = e["pubDate"]

        item_xml = f"""  <item>
    <title>{title_esc}</title>
    <link>{link_esc}</link>
    <guid isPermaLink="false">{guid_esc}</guid>
    <pubDate>{pub_date}</pubDate>
  </item>"""
        items_xml.append(item_xml)

    items_joined = "\n".join(items_xml)

    rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>{html.escape(channel_title)}</title>
  <link>{html.escape(channel_link)}</link>
  <description>{html.escape(channel_description)}</description>
  <language>ja</language>
{items_joined}
</channel>
</rss>
"""
    return rss_xml


def main():
    entries = build_entries()
    rss_xml = build_rss_xml(entries)

    os.makedirs(DOCS_DIR, exist_ok=True)
    out_path = os.path.join(DOCS_DIR, FEED_FILENAME)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(rss_xml)

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Error:", e, file=sys.stderr)
        sys.exit(1)
