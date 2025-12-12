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
SOURCE_API = (
    "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
    "?actor=psyarxivbot.bsky.social&limit=50"
)


DOCS_DIR = "docs"
FEED_FILENAME = "feed.xml"

# OpenAI クライアント（環境変数 OPENAI_API_KEY からキーを読む）
client = OpenAI()


# ==== Bluesky → OSFリンク & 英語タイトル 抽出 ====

def fetch_source_feed_json():
    """Bluesky の public API から PsyArXivBot の投稿を JSON で取得する。"""
    resp = requests.get(SOURCE_API, timeout=30)
    resp.raise_for_status()
    return resp.json()


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
    if not os.environ.get("OPENAI_API_KEY"):
        return en_title

    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": "You are a professional academic psychological translator."
                },
                {
                    "role": "user",
                    "content": (
                        "Translate the following academic psychological paper title into natural Japanese. "
                        "Output ONLY the Japanese title.\n\n"
                        f"{en_title}"
                    )
                }
            ],
            temperature=0.2,
        )

        ja = response.output_text.strip()
        if ja:
            return ja
        return en_title

    except Exception as e:
        print("Translation error:", e)
        return en_title

# ==== エントリ構築 ====

def build_real_entries():
    """Bluesky API の JSON から、日本語タイトル付きエントリを作成する。"""
    try:
        data = fetch_source_feed_json()
    except Exception as e:
        print(f"Error fetching Bluesky feed: {e}")
        return []

    entries = []

    # data["feed"] の中に各ポストが入っている
    for item in data.get("feed", []):
        post = item.get("post", {})
        record = post.get("record", {})

        # ---- テキスト本体 ----
        text = record.get("text", "")
        if not text:
            continue

        # ---- OSF / PsyArXiv のURLを抽出 ----
        url = extract_osf_url(text)
        if not url:
            # OSFリンクを含まないポストはスキップ
            continue

        # ---- 英語タイトルを抽出 ----
        en_title = extract_en_title(text, url)

        # ---- 日本語タイトルに翻訳 ----
        ja_title = ja_title_from_en(en_title)
        full_title = f"{ja_title} ({en_title})"

        # ---- 投稿日時 ----
        created_at = record.get("createdAt")  # 例: "2025-03-21T03:27:33.752Z"
        if created_at:
            try:
                # "Z" を UTC として扱う
                from datetime import datetime, timezone

                dt = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00")
                )
                pub_date = format_datetime(dt.astimezone(timezone.utc))
            except Exception:
                pub_date = format_datetime(datetime.now(timezone.utc))
        else:
            from datetime import datetime, timezone
            pub_date = format_datetime(datetime.now(timezone.utc))

        entries.append(
            {
                "title": full_title,
                "link": url,  # Reeder から直接 OSF/PsyArXiv に飛ぶ
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
