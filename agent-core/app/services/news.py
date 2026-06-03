"""Free, key-less news headlines via RSS/Atom feeds.

The proactive companion uses this to occasionally mention something fresh. We
deliberately avoid any paid news API: a list of public RSS/Atom feeds (see
``settings.news_feeds``) is fetched with ``httpx`` and parsed with the stdlib
XML parser. Everything degrades gracefully — any network or parse failure
yields no headline so the caller falls back to a memory/time topic.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import httpx

from app.config import settings
from app.logging import get_logger

logger = get_logger("services.news")


@dataclass
class Headline:
    title: str
    link: str
    source: str


# Process-wide cache: (fetched_at, headlines). Refreshed at most every
# ``settings.news_cache_seconds`` so we never hammer the feeds.
_cache: tuple[float, list[Headline]] = (0.0, [])
_lock = asyncio.Lock()


def _feeds() -> list[str]:
    return [feed.strip() for feed in settings.news_feeds.split(",") if feed.strip()]


def _local_tag(tag: str) -> str:
    """Strip the XML namespace from a tag (``{http://...}title`` → ``title``)."""
    return tag.rsplit("}", 1)[-1].lower()


def _parse_feed(xml_text: str, source: str) -> list[Headline]:
    """Parse RSS 2.0 (``<item>``) or Atom (``<entry>``) into headlines.

    Best-effort: returns an empty list on any parse error.
    """
    out: list[Headline] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for entry in root.iter():
        if _local_tag(entry.tag) not in {"item", "entry"}:
            continue
        title = ""
        link = ""
        for child in entry:
            ctag = _local_tag(child.tag)
            if ctag == "title" and child.text:
                title = child.text.strip()
            elif ctag == "link" and not link:
                # RSS keeps the URL as element text; Atom uses an href attribute.
                link = (child.text or child.get("href") or "").strip()
        if title:
            out.append(Headline(title=title, link=link, source=source))
    return out


async def _fetch_one(client: httpx.AsyncClient, url: str) -> list[Headline]:
    try:
        response = await client.get(url, timeout=settings.news_fetch_timeout_seconds)
        response.raise_for_status()
    except Exception as exc:  # network / HTTP / timeout — all non-fatal
        logger.debug("news feed fetch failed (%s): %s", url, exc)
        return []
    source = url.split("//", 1)[-1].split("/", 1)[0]
    return _parse_feed(response.text, source)


async def fetch_headlines(force: bool = False) -> list[Headline]:
    """Return recent headlines, refreshing at most every ``news_cache_seconds``."""
    global _cache
    cached_ts, cached = _cache
    if not force and cached and (time.time() - cached_ts) < settings.news_cache_seconds:
        return cached
    async with _lock:
        # Re-check after acquiring the lock — another task may have refreshed.
        cached_ts, cached = _cache
        if not force and cached and (time.time() - cached_ts) < settings.news_cache_seconds:
            return cached
        feeds = _feeds()
        if not feeds:
            return []
        headers = {"User-Agent": "ShadowCompanion/1.0 (+desktop pet)"}
        async with httpx.AsyncClient(follow_redirects=True, headers=headers) as client:
            results = await asyncio.gather(*[_fetch_one(client, url) for url in feeds])
        headlines: list[Headline] = []
        for batch in results:
            headlines.extend(batch)
        if headlines:
            _cache = (time.time(), headlines)
        return headlines


async def random_headline() -> Headline | None:
    """Pick a headline, biased toward the freshest items. ``None`` if unavailable."""
    headlines = await fetch_headlines()
    if not headlines:
        return None
    return random.choice(headlines[:40])
