"""Typed wrappers over the MediaWiki query API."""

from __future__ import annotations

import logging
from typing import Sequence

from ..http import LiquipediaClient
from ..models import WikiPage

logger = logging.getLogger(__name__)

MAX_TITLES_PER_REQUEST = 50


def fetch_page(client: LiquipediaClient, title: str) -> WikiPage:
    return fetch_pages(client, [title])[title]


def fetch_pages(
    client: LiquipediaClient,
    titles: Sequence[str],
    chunk_size: int = MAX_TITLES_PER_REQUEST,
) -> dict[str, WikiPage]:
    """Fetch wikitext for many titles, batched up to 50 titles per request.

    Returns a mapping keyed by the *requested* title; normalization
    (``energy`` -> ``Energy``) and redirects are followed, and the resulting
    WikiPage carries the canonical title.
    """
    requested = list(dict.fromkeys(titles))
    result: dict[str, WikiPage] = {}
    for start in range(0, len(requested), chunk_size):
        chunk = requested[start : start + chunk_size]
        data = client.get(
            action="query",
            prop="revisions",
            rvprop="content",
            rvslots="main",
            redirects="1",
            titles="|".join(chunk),
        )
        query = data["query"]
        alias: dict[str, str] = {}
        for entry in query.get("normalized", []) + query.get("redirects", []):
            alias[entry["from"]] = entry["to"]
        pages: dict[str, WikiPage] = {}
        for page in query.get("pages", []):
            if page.get("missing") or page.get("invalid"):
                pages[page["title"]] = WikiPage(page["title"], None, missing=True)
            else:
                content = page["revisions"][0]["slots"]["main"]["content"]
                pages[page["title"]] = WikiPage(page["title"], content)
        for title in chunk:
            canonical = title
            seen = set()
            while canonical in alias and canonical not in seen:
                seen.add(canonical)
                canonical = alias[canonical]
            if canonical in pages:
                result[title] = pages[canonical]
            else:
                logger.warning(
                    "title missing from API response",
                    extra={"fields": {"title": title, "canonical": canonical}},
                )
                result[title] = WikiPage(canonical, None, missing=True)
    return result
