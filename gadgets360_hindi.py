#!/usr/bin/env python3
"""Scrape hindi.gadgets360.com/news and write an RSS 2.0 feed.

The site blocks plain datacenter requests (Akamai 403); curl_cffi with a
Chrome TLS fingerprint gets through. Output: gadgets360-hindi.xml at repo root.
Newest items are enriched with article body text (<description>) and a lead
image re-hosted in images/ (the CDN 403s plain fetches too, so the n8n server
can only fetch the re-hosted copy). Output feed stays title/link/date-safe for
the digest workflow.
"""
import json
import os
import re
import sys
import time
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

IMG_DIR = 'images'
IMG_BASE = 'https://raw.githubusercontent.com/mobiletelco/mt-feeds/main/images'
IMG_EXT = {'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/png': '.png', 'image/gif': '.gif'}
ENRICH_COUNT = 15


def enrich(it):
    """Fetch the article page; add it['desc'] (body text) and it['image'] (og:image)."""
    try:
        r = creq.get(it['link'], impersonate='chrome', timeout=30)
        if r.status_code != 200:
            print(f"enrich fetch HTTP {r.status_code}: {it['link']}", file=sys.stderr)
            return
        soup = BeautifulSoup(r.text, 'lxml')
        og = soup.find('meta', property='og:image')
        if og and og.get('content'):
            it['image'] = og['content'].strip()
        body = soup.select_one('.content_text') or soup.find('article')
        if body:
            paras = [p.get_text(' ', strip=True) for p in body.find_all('p')]
            text = ' '.join(t for t in paras if t and not t.lower().startswith('photo credit'))
            if text.strip():
                it['desc'] = text[:1500].strip()
    except Exception as e:
        print(f"enrich failed: {it['link']}: {e}", file=sys.stderr)


def host_image(it):
    """Download the lead image (impersonated) and re-host it in the repo."""
    img = it.get('image')
    if not img:
        return
    m = re.search(r'-news-(\d+)$', it['link'])
    if not m:
        return
    try:
        r = creq.get(img, impersonate='chrome', timeout=30)
        ct = (r.headers.get('content-type') or '').split(';')[0].strip().lower()
        ext = IMG_EXT.get(ct)
        if r.status_code != 200 or not ext or len(r.content) < 5000:
            print(f"image skip (HTTP {r.status_code}, {ct}, {len(r.content)}b): {img}", file=sys.stderr)
            return
        name = f"{IMG_DIR}/{m.group(1)}{ext}"
        old = None
        if os.path.exists(name):
            old = open(name, 'rb').read()
        if old != r.content:
            with open(name, 'wb') as f:
                f.write(r.content)
        it['hosted_image'] = f"{IMG_BASE}/{m.group(1)}{ext}"
        it['hosted_type'] = ct
    except Exception as e:
        print(f"image fetch failed: {img}: {e}", file=sys.stderr)


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

    os.makedirs(IMG_DIR, exist_ok=True)
    for it in items[:ENRICH_COUNT]:
        enrich(it)
        time.sleep(0.3)
        host_image(it)
    # prune repo images no longer referenced by the live feed window
    keep = {os.path.basename(it['hosted_image']) for it in items if it.get('hosted_image')}
    for f in os.listdir(IMG_DIR):
        if f not in keep:
            os.remove(os.path.join(IMG_DIR, f))

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
        if it.get('desc'):
            parts.append(f"<description>{escape(it['desc'])}</description>")
        img = it.get('hosted_image') or it.get('image')
        if img:
            mt = it.get('hosted_type') or 'image/jpeg'
            parts.append(f'<enclosure url="{escape(img)}" type="{mt}"/>')
        parts.append('</item>')
    parts.append('</channel>')
    parts.append('</rss>')

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts) + '\n')
    print(f'wrote {OUT} with {min(len(items),30)} items ({len(items)} parsed)')

if __name__ == '__main__':
    main()
