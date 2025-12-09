#!/usr/bin/env python3
import os
import re
import sys
import html
from datetime import datetime, timezone
from email.utils import format_datetime

import requests
from bs4 import BeautifulSoup
from googletrans import Translator

# 入力元: PsyArXiv（OSF）のプレプリントRSS
# https://osf.io/preprints/psyarxiv/ は RSS として購読可能
SOURCE_RSS = "https://osf.io/preprints/psyarxiv/"


# Google翻訳クライアント（無料）
translator = Translator(service_urls=["translate.googleapis.com"])


def fetch_source_feed_xml():
    """PsyArXiv/OSF のRSSを取得して BeautifulSoup(xml) にする。"""
    resp = requests.get(SOURCE_RSS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.content, "xml")



def extract_psyarxiv_url(text: str | None) -> str | None:
    """
    テキスト中から PsyArXiv 関連のURLを1つ拾う。
    psyarxiv.com か osf.io（PsyArXivプレプリント）を対象とする。
    見つからなければ None。
    """
    if not text:
        return None

    # まず psyarxiv.com を優先して探す
    m = re.search(r"https?://psyarxiv\.com/\S+", text)
    if m:
        return m.group(0).rstrip(").,]")

    # 見つからなければ osf.io のURLを探す
    m = re.search(r"https?://osf\.io/\S+", text)
    if m:
        return m.group(0).rstrip(").,]")

    return None



def fetch_psyarxiv_title(url: str) -> str | None:
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


def ja_title_from_en(en_title: str) -> str:
    """英語タイトルを日本語に翻訳（失敗したらそのまま返す）。"""
    try:
        res = translator.translate(en_title, src="en", dest="ja")
        ja = res.text.strip()
        return ja or en_title
    except Exception:
        return en_title


def build_entries():
    """Bluesky RSS → PsyArXiv URLとタイトルを抜き出してエントリリストを返す。"""
    bs_feed = fetch_bluesky_feed_xml()
    entries = []

    for item in bs_feed.find_all("item"):
        raw_title = (item.title.string or "").strip() if item.title else ""
        description = (item.description.string or "").strip() if item.description else ""
        text = raw_title or description

        psy_url = extract_psyarxiv_url(text)
        if not psy_url:
            continue

        en_title = fetch_psyarxiv_title(psy_url) or text
        ja_title = ja_title_from_en(en_title)
        full_title = f"{ja_title} ({en_title})"

        # pubDate はあればそれを使う、なければ現在時刻
        if item.pubDate and item.pubDate.string:
            pub_date = item.pubDate.string.strip()
        else:
            pub_date = format_datetime(datetime.now(timezone.utc))

        entries.append(
            {
                "title": full_title,
                "link": psy_url,
                "pubDate": pub_date,
            }
        )

    return entries


def build_rss_xml(entries):
    """entriesリストからシンプルなRSS 2.0のXML文字列を生成する。"""
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

        item_xml = f"""
    <item>
      <title>{title_esc}</title>
      <link>{link_esc}</link>
      <guid isPermaLink="false">{guid_esc}</guid>
      <pubDate>{pub_date}</pubDate>
      <description><![CDATA[PsyArXiv: {e["link"]}]]></description>
    </item>"""
        items_xml.append(item_xml)

    items_joined = "\n".join(items_xml)

    rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{html.escape(channel_title)}</title>
    <link>{html.escape(channel_link)}</link>
    <description>{html.escape(channel_description)}</description>
    <language>ja</language>{items_joined}
  </channel>
</rss>
"""
    return rss_xml


def main():
    entries = build_entries()
    # エントリが0件でも、とりあえず空フィードとして出す
    rss_xml = build_rss_xml(entries)

    docs_dir = "docs"
    os.makedirs(docs_dir, exist_ok=True)
    out_path = os.path.join(docs_dir, "feed.xml")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(rss_xml)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Error:", e, file=sys.stderr)
        sys.exit(1)
