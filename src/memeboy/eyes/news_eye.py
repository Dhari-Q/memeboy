#!/usr/bin/env python
"""Quant machine: news eye (stdlib only). Pulls RSS, dedupes by link hash,
stores the freshest items in state/news.json. Silent watchdog (no delivery)."""
import hashlib
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


NEWS = os.path.join(STATE, "news.json")
MAX_ITEMS = 120
TIMEOUT = 15

FEEDS = [
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("decrypt", "https://decrypt.co/feed"),
    ("theblock", "https://www.theblock.co/rss.xml"),
    ("google_memecoin", "https://news.google.com/rss/search?q=%22memecoin%22+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("google_solana", "https://news.google.com/rss/search?q=%22solana%22+when:1d&hl=en-US&gl=US&ceid=US:en"),
]

TAG_RE = re.compile(r"<[^>]+>")


def clean(txt):
    if not txt:
        return ""
    txt = txt.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    return TAG_RE.sub("", txt).strip()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "quant-machine/0.1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_rss(xml_text, source):
    # XXE/billion-laughs guard: feeds with DOCTYPE/ENTITY declarations are
    # rejected outright (public RSS should not carry them).
    if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        return []
    out = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return out
    for item in root.iter("item"):
        try:
            title = clean(item.findtext("title"))
            link = clean(item.findtext("link"))
            pub = clean(item.findtext("pubDate")) or clean(item.findtext("published"))
            desc = clean(item.findtext("description"))[:300]
            if title and link:
                out.append({"source": source, "title": title, "link": link,
                            "pub": pub, "desc": desc,
                            "sha": hashlib.sha256(link.encode()).hexdigest()[:16]})
        except Exception:
            continue
    return out


def main():
    items = {}
    errors = []
    for source, url in FEEDS:
        try:
            for it in parse_rss(fetch(url), source):
                items.setdefault(it["sha"], it)
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__} {exc}")
    rows = sorted(items.values(), key=lambda x: x.get("pub") or "", reverse=True)[:MAX_ITEMS]
    payload = {"ts": int(time.time() * 1000), "count": len(rows), "errors": errors, "items": rows}
    os.makedirs(STATE, exist_ok=True)
    tmp = NEWS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, NEWS)
    # silent


if __name__ == "__main__":
    main()
