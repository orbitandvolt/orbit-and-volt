#!/usr/bin/env python3
"""Refresh data/news.json and data/launches.json for Orbit & Volt.

Runs every 30 minutes in GitHub Actions (.github/workflows/update-data.yml) and can be run by hand:
    python scripts/update_data.py

Standard library only. Stores headline, link, source and time for each story; never article text or images,
so the site links to publishers instead of republishing them.
"""
import email.utils
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UA = "OrbitAndVolt/1.0 (+news aggregator; links to publishers)"
MAX_AGE_DAYS = 7
MAX_ITEMS = 150

# kind: "tesla" = Tesla-focused feed, "mixed" = covers both, "space" / "general" = keep only keyword matches
FEEDS = [
    ("Electrek", "https://electrek.co/guides/tesla/feed/", "tesla"),
    ("InsideEVs", "https://insideevs.com/rss/make/tesla/", "tesla"),
    ("Not a Tesla App", "https://www.notateslaapp.com/rss", "tesla"),
    ("Teslarati", "https://www.teslarati.com/feed/", "mixed"),
    ("SpaceNews", "https://spacenews.com/feed/", "space"),
    ("Spaceflight Now", "https://spaceflightnow.com/feed/", "space"),
    ("NASASpaceflight", "https://www.nasaspaceflight.com/feed/", "space"),
    ("Space.com", "https://www.space.com/feeds/all", "space"),
    ("CNBC", "https://www.cnbc.com/id/10000101/device/rss/rss.html", "general"),
]

TESLA_WORDS = r"tesla|tsla|cybertruck|cybercab|robotaxi|model [3ysx]\b|semi truck|tesla semi|\bfsd\b|full self[- ]driving|autopilot|optimus|megapack|powerwall|supercharg|gigafactory|giga (texas|berlin|shanghai|nevada)"
SPACEX_WORDS = r"spacex|starship|super heavy|falcon (9|heavy)|starlink|crew dragon|cargo dragon|\bdragon\b|raptor engine|starbase"
TESLA_RE = re.compile(TESLA_WORDS, re.I)
SPACEX_RE = re.compile(SPACEX_WORDS, re.I)


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def text(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None:
            if found.text and found.text.strip():
                return found.text.strip()
            if found.get("href"):
                return found.get("href").strip()
    return ""


def parse_date(s):
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", "", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def categorize(title, kind):
    t, s = bool(TESLA_RE.search(title)), bool(SPACEX_RE.search(title))
    if kind == "tesla":
        t = t or not s
    # "mixed" feeds (Teslarati) also run Musk-adjacent stories (xAI, X), so they keep only keyword matches
    if t and s:
        return "both"
    return "tesla" if t else "spacex" if s else None


def parse_feed(source, url, kind, now):
    root = ET.fromstring(fetch(url))
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item") or root.findall(f".//{atom}entry")
    out = []
    for it in items:
        title = clean(text(it, "title", f"{atom}title"))
        link = text(it, "link", f"{atom}link")
        when = parse_date(text(it, "pubDate", f"{atom}published", f"{atom}updated",
                               "{http://purl.org/dc/elements/1.1/}date"))
        if not title or not link.startswith("http") or not when:
            continue
        if when < now - timedelta(days=MAX_AGE_DAYS) or when > now + timedelta(hours=1):
            continue
        cat = categorize(title, kind)
        if cat:
            out.append({"title": title, "url": link, "source": source,
                        "published": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "category": cat})
    return out


def norm(title):
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def update_news(now):
    stories, errors = [], []
    for source, url, kind in FEEDS:
        try:
            got = parse_feed(source, url, kind, now)
            stories += got
            print(f"  {source}: {len(got)} stories")
        except Exception as e:  # one broken feed must not stop the others
            errors.append(f"{source}: {e}")
            print(f"  {source}: FAILED {e}", file=sys.stderr)
    seen, unique = set(), []
    for s in sorted(stories, key=lambda s: s["published"], reverse=True):
        key = norm(s["title"])
        if key in seen or s["url"] in seen:
            continue
        seen.update({key, s["url"]})
        unique.append(s)
    return unique[:MAX_ITEMS], errors


def update_launches():
    url = "https://ll.thespacedevs.com/2.2.0/launch/upcoming/?lsp__name=SpaceX&limit=12&mode=list"
    results = json.loads(fetch(url))["results"]
    out = []
    for l in results:
        out.append({
            "name": l.get("name"),
            "mission": l.get("mission"),
            "net": l.get("net"),
            "precision": (l.get("net_precision") or {}).get("abbrev") or "UNK",
            "status": (l.get("status") or {}).get("abbrev"),
            "status_name": (l.get("status") or {}).get("name"),
            "pad": l.get("pad"),
            "location": l.get("location"),
        })
    return out


def write(name, payload):
    DATA.mkdir(exist_ok=True)
    (DATA / name).write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    print("News:")
    stories, errors = update_news(now)
    if stories:
        write("news.json", {"generated_at": stamp, "count": len(stories), "errors": errors, "stories": stories})
    else:
        print("No stories fetched; keeping the previous news.json", file=sys.stderr)
    print("Launches:")
    try:
        launches = update_launches()
        write("launches.json", {"generated_at": stamp, "source": "Launch Library 2 (The Space Devs)", "launches": launches})
        print(f"  {len(launches)} upcoming SpaceX launches")
    except Exception as e:
        print(f"  FAILED {e}; keeping the previous launches.json", file=sys.stderr)
    return 0 if stories else 1


if __name__ == "__main__":
    sys.exit(main())
