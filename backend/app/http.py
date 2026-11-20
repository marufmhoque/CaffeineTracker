"""Shared polite HTTP client: identifies itself, honours 429 Retry-After, limited retries."""

from __future__ import annotations

import logging
import time

import httpx

from . import config

log = logging.getLogger(__name__)


def make_client(**kwargs) -> httpx.Client:
    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json, text/html;q=0.9, */*;q=0.5"}
    headers.update(kwargs.pop("headers", {}))
    return httpx.Client(
        timeout=kwargs.pop("timeout", config.HTTP_TIMEOUT),
        follow_redirects=True,
        headers=headers,
        **kwargs,
    )


def get_with_retry(
    client: httpx.Client, url: str, *, retries: int = 2, max_wait: float = 30.0, **kwargs
) -> httpx.Response:
    """GET with backoff on 429/5xx. Raises httpx.HTTPStatusError on final failure."""
    attempt = 0
    while True:
        resp = client.get(url, **kwargs)
        if resp.status_code < 400:
            return resp
        retryable = resp.status_code == 429 or resp.status_code >= 500
        if not retryable or attempt >= retries:
            resp.raise_for_status()
        wait = 2.0 * (2**attempt)
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                wait = float(ra)
            except ValueError:
                pass
        wait = min(wait, max_wait)
        log.info("HTTP %s from %s; retrying in %.1fs", resp.status_code, url, wait)
        time.sleep(wait)
        attempt += 1
