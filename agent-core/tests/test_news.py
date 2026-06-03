from __future__ import annotations

import pytest

from app.services import news
from app.services.news import Headline, _parse_feed

RSS_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Example</title>
  <item><title>First headline</title><link>https://example.com/1</link></item>
  <item><title>Second headline</title><link>https://example.com/2</link></item>
</channel></rss>"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom</title>
  <entry><title>Atom one</title><link href="https://example.com/a1"/></entry>
  <entry><title>Atom two</title><link href="https://example.com/a2"/></entry>
</feed>"""


def test_parse_rss() -> None:
    items = _parse_feed(RSS_SAMPLE, "example.com")
    assert [h.title for h in items] == ["First headline", "Second headline"]
    assert items[0].link == "https://example.com/1"
    assert items[0].source == "example.com"


def test_parse_atom_uses_href() -> None:
    items = _parse_feed(ATOM_SAMPLE, "example.com")
    assert [h.title for h in items] == ["Atom one", "Atom two"]
    assert items[0].link == "https://example.com/a1"


def test_parse_garbage_is_empty() -> None:
    assert _parse_feed("not xml at all <<<", "x") == []
    assert _parse_feed("", "x") == []


@pytest.mark.asyncio
async def test_random_headline_none_when_empty(monkeypatch) -> None:
    async def fake_fetch(force: bool = False):
        return []

    monkeypatch.setattr(news, "fetch_headlines", fake_fetch)
    assert await news.random_headline() is None


@pytest.mark.asyncio
async def test_random_headline_returns_one(monkeypatch) -> None:
    async def fake_fetch(force: bool = False):
        return [Headline("A", "", "s"), Headline("B", "", "s")]

    monkeypatch.setattr(news, "fetch_headlines", fake_fetch)
    headline = await news.random_headline()
    assert headline is not None
    assert headline.title in {"A", "B"}
