import requests
from bs4 import BeautifulSoup
from datetime import datetime
from feedgen.feed import FeedGenerator
from openai import OpenAI

# =========================
# 設定
# =========================

BLUESKY_URL = "https://bsky.app/profile/psyarxivbot.bsky.social"
OUTPUT_PATH = "docs/feed.xml"
AUTHOR_DISPLAY_LIMIT = 3
MODEL_TRANSLATE = "gpt-4.1-mini"

client = OpenAI()  # OPENAI_API_KEY 必須


# =========================
# 共通ユーティリティ
# =========================

def get_soup(url: str):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


# =========================
# Bluesky → OSF URL 取得
# =========================

def fetch_osf_links():
    soup = get_soup(BLUESKY_URL)
    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "osf.io/" in href:
            if href.startswith("/"):
                href = "https://bsky.app" + href
            links.append(href)

    # 重複除去
    return list(dict.fromkeys(links))


# =========================
# OSF 論文情報取得
# =========================

def fetch_osf_metadata(osf_url: str):
    soup = get_soup(osf_url)

    # タイトル
    title_tag = soup.find("h1")
    title_en = title_tag.get_text(strip=True) if title_tag else "Untitled"

    # 著者（2025年以降UI対応）
    authors = []
    for a in soup.select('a[href^="/profile/"]'):
        name = a.get_text(strip=True)
        if name:
            authors.append(name)

    authors = list(dict.fromkeys(authors))

    return title_en, authors


def format_authors(authors):
    if not authors:
        return ""
    if len(authors) <= AUTHOR_DISPLAY_LIMIT:
        return ", ".join(authors)
    return ", ".join(authors[:AUTHOR_DISPLAY_LIMIT]) + " et al."


# =========================
# 翻訳
# =========================

def translate_title(title_en: str) -> str:
    res = client.chat.completions.create(
        model=MODEL_TRANSLATE,
        messages=[
            {"role": "system", "content": "Translate academic paper titles into natural Japanese."},
            {"role": "user", "content": title_en}
        ],
        temperature=0.2,
    )
    return res.choices[0].message.content.strip()


# =========================
# RSS生成
# =========================

def build_feed():
    fg = FeedGenerator()
    fg.title("PsyArXiv bot (日本語タイトル付き)")
    fg.link(href=BLUESKY_URL)
    fg.description(
        "psyarxivbot.bsky.social の投稿から PsyArXiv 論文を取得し、"
        "日本語タイトル＋英語タイトル＋著者情報を配信する非公式RSS"
    )
    fg.language("ja")

    osf_links = fetch_osf_links()

    for osf_url in osf_links:
        title_en, authors = fetch_osf_metadata(osf_url)
        title_ja = translate_title(title_en)
        author_text = format_authors(authors)

        fe = fg.add_entry()
        fe.id(osf_url + "#ja")
        fe.link(href=osf_url)
        fe.pubDate(datetime.utcnow())

        fe.title(title_ja)

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
