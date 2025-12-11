#!/usr/bin/env python3
import os
import sys
import re
import html
from datetime import datetime, timezone
from email.utils import format_datetime

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# ==== 設定 ====
# 入力元: Bluesky の PsyArXivBot プロフィール RSS
SOURCE_RSS = "https://bsky.app/profile/psyarxivbot.bsky.social/rss"

DOCS_DIR = "docs"
FEED_FILENAME = "feed.xml"

# OpenAI クライアント（環境変数 OPENAI_API_KEY からキーを読む）
client = OpenAI()


# ==== Bluesky → OSFリンク & 英語タイトル 抽出 ====

def fetch_source_feed_xml():
    """Bluesky の RSS を取得して BeautifulSoup(xml) にする。"""
    resp = requests.get(SOURCE_RSS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.content, "xml")


def extract_osf_url(text: str | None) -> str | None:
    """テキスト中から OSF/PsyArXiv の URL を1つ拾う。見つからなければ None。"""
    if not text:
        return None

    # psyarxiv.com を優先
    m = re.search(r"https?://psyarxiv\.com/\S+", text)
    if m:
        return m.group(0).rstrip(").,]")

    # なければ osf.io
    m = re.search(r"https?://osf\.io/\S+", text)
    if m:
        return m.group(0).rstrip(").,]")

    return None


def extract_en_title(text: str, url: str) -> str:
    """
    Bluesky のポスト本文から英語タイトル部分を取り出す。
    URL の前後を削って、残りをタイトルとみなす。
    """
    t = text.replace(url, "")
    # URL直前のコロン・ダッシュなどを削る
    t = re.sub(r"[:\-–—\s]+$", "", t.strip())
    if not t:
        return url
    return t


# ==== 英語タイトル → 日本語タイトル ====

def ja_title_from_en(en_title: str) -> str:
    """
    gpt-5-nano を使って英語タイトルを自然な日本語タイトルに翻訳。
    失敗したら元の英語タイトルをそのまま返す。
    """
    if not os.environ.get("OPENAI_API_KEY"):
        # APIキーが無いときは英語のまま
        return en_title

    try:
        response = client.responses.create(
            model="gpt-5.1-mini",  # gpt-5-nano 系の低コストモデル
            input=(
                "あなたは学術論文タイトルの専門翻訳者です。"
                "次の英語の論文タイトルを、学術的に自然な日本語タイトルに翻訳してください。"
                "出力は日本語タイトルのみを1行で書き、余計な説明や引用符は一切付けないでください。\n\n"
                f"{en_title}"
            ),
            temperature=0.2,
        )
        ja = response.output[0].content[0].text.strip()
        return ja or en_title
    except Exception as e:
        print(f"OpenAI translation error: {e}", file=sys.stderr)
        return en_title


# ==== エントリ構築 ====

def build_real_entries():
    """Bluesky RSS から実データを読み、日本語タイトル付きエントリを作る。"""
    feed = fetch_source_feed_xml()
    entries = []

    for item in feed.find_all("item"):
        # ポスト本文（title を優先）
        if item.title and item.title.string:
            text = item.title.string.strip()
        elif item.description and item.description.string:
            text = item.description.string.strip()
        else:
            continue

        url = extract_osf_url(text)
        if not url:
            # OSFリンクを含まないポストはスキップ
            continue

        en_title = extract_en_title(text, url)
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
                "link": url,  # Reederから直接 OSF/PsyArXiv へ飛ぶ
                "pubDate": pub_date,
            }
        )

    return entries


def build_test_entries():
    """トラブル時のフォールバック用テスト2件。"""
    now = format_datetime(datetime.now(timezone.utc))
    return [
        {
            "title": "テスト論文その1 (Test paper one)",
            "link": "https://psyarxiv.com/abcd1",
            "pubDate": now,
        },
        {
            "title": "テスト論文その2 (Test paper two)",
            "link": "https://psyarxiv.com/abcd2",
            "pubDate": now,
        },
    ]


def build_entries():
    """本番エントリを作成し、ダメならテストにフォールバックする。"""
    try:
        entries = build_real_entries()
        if entries:
            return entries
        print("No entries from Bluesky feed, falling back to test entries", file=sys.stderr)
    except Exception as e:
        print(f"Error fetching Bluesky feed: {e}", file=sys.stderr)

    return build_test_entries()


# ==== RSS 生成 ====

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
