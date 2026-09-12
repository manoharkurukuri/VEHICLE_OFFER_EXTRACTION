"""Opt-in live scrape check for bot-protected dealer URLs.

Skipped by default (hits real sites + launches Playwright). Run it explicitly:

    RUN_LIVE_SCRAPE=1 python -m pytest tests/test_live_scrape.py -s -v

Override the URLs with a comma-separated list:

    RUN_LIVE_SCRAPE=1 LIVE_SCRAPE_URLS="https://a.com,https://b.com" \
        python -m pytest tests/test_live_scrape.py -s -v
"""

import os

import pytest

from app.service.scraper import ScrapingError, get_website_content_from_url

_DEFAULT_URLS = [
    "https://www.heywardallen.com/new-vehicles/new-vehicle-specials/",
    "https://www.heywardallencadillac.com/new-vehicles/dealer-specials/",
]

_URLS = [
    u.strip()
    for u in os.getenv("LIVE_SCRAPE_URLS", ",".join(_DEFAULT_URLS)).split(",")
    if u.strip()
]

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_SCRAPE") != "1",
    reason="Set RUN_LIVE_SCRAPE=1 to run the live bot-protection scrape check.",
)


@pytest.mark.parametrize("url", _URLS)
def test_bot_protected_url_returns_body(url):
    try:
        data = get_website_content_from_url(url)
    except ScrapingError as exc:
        pytest.fail(f"BLOCKED: {url} -> {exc}")

    body = data.get("body", "")
    print(f"\nOK {url}\n  title: {data.get('title', '')[:80]}")
    print(f"  body chars: {len(body)}")
    print(f"  preview: {body[:300].replace(chr(10), ' ')}")
    assert body.strip(), f"Empty body for {url}"
