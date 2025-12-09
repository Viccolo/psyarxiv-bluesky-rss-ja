#!/usr/bin/env python3
import os
import html
from datetime import datetime, timezone
from email.utils import format_datetime

DOCS_DIR = "docs"
FEED_FILENAME = "feed.xml"


def build_entries():
    """テスト用に固定2件だけ返す。外部アクセスなし。"""
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


def build_rss_xml(entries):
    """entries からシンプルな RSS 2.0 を構成。"""
    channel_title = "PsyArXiv bot (日本語タイトル付き)"
    channel_link = "https://bsky.app/profile/psyarxivbot.bsky.social"
    channel_description = (
        "テスト用：固定2件のダミーエントリを配信するRSSフィード"
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
    main()
