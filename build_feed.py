import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from feedgen.feed import FeedGenerator
from openai import OpenAI

# =========================
# 設定
# =========================

BLUESKY_FEED_URL = "https://bsky.app/profile/psyarxivbot.bsky.social"
OUTPUT_PATH = "docs/feed.xml"

# 著者表示数（ここを変えるだけ）
AUTHOR_DISPLAY_LIMIT = 3   # 例：3 → 3人まで表示、それ以上は et al.

MODEL_TRANSLATE = "gpt-4.1-mini"  # 安定動作確認済み

client = OpenAI()  # OPENAI_API_KEY を使用


# =========================
# ユーティリティ
# =========================

def fetch_html(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception:
        return None


def fetch_authors(osf_url: str) -> list[str]:
    """
    PsyArXiv 論文ページの Authors セクションから
    表示されている名前をそのまま取得（厳密性は追わない）
    """
    soup = fetch_html(osf_url)
    if soup is None:
        return []

    authors = []

    # 2025年以降のUI対応：profileリンクを持つ a タグ
    for a in soup.select('a[href^="/profile/"]'):
        name = a.get_text(strip=True)
        if name:
            authors.append(name)

    # 重複除去（順序保持）
    return list(dict.fromkeys(authors))


def format_authors(authors: list[str], limit: int) -> str:
    """
    表示人数は後で自由に変更できるように分離
    """
    if not authors:
        return ""

    if len(authors) <= limit:
        return ", ".join(authors)

    return ", ".join(authors[:limit]) + " et al."


def translate_title(title_en: str) -> str:
    """
    英語タイトル → 日本語タイトル
    """
    try:
        res = client.chat.completions.create(
            model=MODEL_TRANSLATE,
            messages=[
                {
                    "role": "system",
                    "content": "Translate academic paper titles into natural Japanese."
                },
                {
                    "role": "user",
                    "content": title_en
                }
            ],
            temperature=0.2,
        )
        return res.choices[0].message.content.strip()
    except Exception:
        return title_en  # 失敗時は英語のまま


# =========================
# メイン処理
# =========================

def build_feed():
    fg = FeedGenerator()
    fg.load_extension("dc", atom=False, rss=True)

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

    papers = fetch_papers_somehow()  # ← 既存処理をそのまま使う想定

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

        # タイトルは日本語のみ（RSS一覧で視認性優先）
        fe.title(title_ja)

        # description に英語タイトル＋著者
        desc = f"EN: {title_en}"
        if author_text:
            desc += f" | Authors: {author_text}"

        fe.description(desc)

    fg.rss_file(OUTPUT_PATH, encoding="utf-8")


# =========================
# 実行
# =========================

if __name__ == "__main__":
    build_feed()
