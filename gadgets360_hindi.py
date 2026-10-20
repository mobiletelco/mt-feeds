#!/usr/bin/env python3
"""Scrape hindi.gadgets360.com/news and write an RSS 2.0 feed.

The site blocks plain datacenter requests (Akamai 403); curl_cffi with a
Chrome TLS fingerprint gets through. Output: gadgets360-hindi.xml at repo root.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from xml.sax.saxutils import escape

from bs4 import BeautifulSoup
from curl_cffi import requests as creq

URL = 'https://hindi.gadgets360.com/news'
OUT = 'gadgets360-hindi.xml'
STATE = 'g360-state.json'
IST = timezone(timedelta(hours=5, minutes=30))

HI_MONTHS = {
    'जनवरी': 1, 'फरवरी': 2, 'मार्च': 3, 'अप्रैल': 4, 'मई': 5, 'जून': 6,
    'जुलाई': 7, 'अगस्त': 8, 'सितंबर': 9, 'सितम्बर': 9, 'अक्टूबर': 10,
    'नवंबर': 11, 'नवम्बर': 11, 'दिसंबर': 12, 'दिसम्बर': 12,
}

def parse_hi_date(text):
    # "Written by हेमन्त कुमार, 20 अक्टूबर 2026" -> datetime (IST midnight)
    m = re.search(r'(\d{1,2})\s+(\S+)\s+(\d{4})', text or '')
    if not m:
        return None
    day, mon, year = m.group(1), m.group(2), m.group(3)
    month = HI_MONTHS.get(mon)
    if not month:
        return None
    try:
        return datetime(int(year), month, int(day), tzinfo=IST)
    except ValueError:
        return None

def main():
    r = creq.get(URL, impersonate='chrome', timeout=40)
    if r.status_code != 200:
        print(f'fetch failed: HTTP {r.status_code}', file=sys.stderr)
        sys.exit(1)
    soup = BeautifulSoup(r.text, 'lxml')

    items = []
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if not re.search(r'-news-\d+$', href):
            continue
        if not href.startswith('http'):
            continue
        if href in seen:
            continue
        title = ''
        h3 = a.find('h3')
        if h3:
            title = h3.get_text(' ', strip=True)
        if not title:
            img = a.find('img')
            if img and img.get('alt'):
                title = img['alt'].strip()
        if not title:
            title = a.get_text(' ', strip=True)
        if not title:
            continue
        seen.add(href)
        # date from the enclosing list item's dateline, if present
        pub = None
        li = a.find_parent('li')
        if li:
            dl = li.select_one('.dateline')
            if dl:
                pub = parse_hi_date(dl.get_text(' ', strip=True))
        items.append({'title': title, 'link': href, 'pub': pub})

    if not items:
        print('no items parsed - page structure changed?', file=sys.stderr)
        sys.exit(1)

    now = datetime.now(IST)
    now_s = now.isoformat()

    state = {}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE))
        except Exception:
            state = {}
    # first-seen wins; backdate brand-new items stamped during this run
    for it in items:
        if it['link'] not in state:
            state[it['link']] = now_s
        it['first_seen'] = state[it['link']]
    # prune urls no longer in the feed window
    live = {it['link'] for it in items}
    state = {u: t for u, t in state.items() if u in live}
    with open(STATE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=0)

    def fmt(dt):
        return dt.strftime('%a, %d %b %Y %H:%M:%S +0530')

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        '<channel>',
        '<title>Gadgets360 Hindi News</title>',
        f'<link>{URL}</link>',
        '<description>Latest tech news from Gadgets360 Hindi (hindi.gadgets360.com)</description>',
        '<language>hi</language>',
        f'<lastBuildDate>{fmt(now)}</lastBuildDate>',
    ]
    for it in items[:30]:
        try:
            pub = datetime.fromisoformat(it['first_seen'])
        except Exception:
            pub = now
        parts.append('<item>')
        parts.append(f"<title>{escape(it['title'])}</title>")
        parts.append(f"<link>{escape(it['link'])}</link>")
        parts.append(f"<guid isPermaLink=\"true\">{escape(it['link'])}</guid>")
        parts.append(f'<pubDate>{fmt(pub)}</pubDate>')
        parts.append('</item>')
    parts.append('</channel>')
    parts.append('</rss>')

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts) + '\n')
    print(f'wrote {OUT} with {min(len(items),30)} items ({len(items)} parsed)')

if __name__ == '__main__':
    main()
