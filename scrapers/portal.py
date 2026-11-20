"""Authenticated importer portals (Cafe Imports, Atlas, Ally, Olam/ofi, Sucafina, ...).

Each portal is described in data/portals.json (see data/portals.example.json). A Playwright browser logs in
with credentials read from environment variables named in the config, downloads the current spot sheet,
and drops it into data/spot_sheets/ where the dropzone parser ingests it like any emailed/uploaded sheet.

Playwright is optional: `pip install playwright && playwright install chromium`.
Portal markup differs per importer and changes over time — selectors live in config, not code.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from backend.app import config

log = logging.getLogger(__name__)


def load_portals(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or config.PORTALS_FILE
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        portals = json.load(f).get("portals", [])
    return [p for p in portals if p.get("enabled", False)]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def download_spot_sheet(portal: dict[str, Any], dest_dir: Path | None = None) -> Path:
    """Log into a portal and save its spot sheet into the dropzone. Returns the saved path."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - optional dependency
        raise RuntimeError("Playwright is not installed: pip install playwright && playwright install chromium") from e

    user = os.getenv(portal["username_env"], "")
    password = os.getenv(portal["password_env"], "")
    if not user or not password:
        raise RuntimeError(
            f"{portal['importer']}: set {portal['username_env']} and {portal['password_env']} in .env"
        )
    dest_dir = dest_dir or config.SPOT_SHEETS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(accept_downloads=True)
            page = ctx.new_page()
            page.goto(portal["login_url"], wait_until="domcontentloaded")
            page.fill(portal["username_selector"], user)
            page.fill(portal["password_selector"], password)
            page.click(portal["submit_selector"])
            page.wait_for_load_state("networkidle")
            if portal.get("spot_sheet_url"):
                page.goto(portal["spot_sheet_url"], wait_until="networkidle")
            with page.expect_download(timeout=60_000) as dl_info:
                page.click(portal["download_selector"])
            download = dl_info.value
            suffix = Path(download.suggested_filename).suffix or ".csv"
            out = dest_dir / f"{_slug(portal['importer'])}_{date.today():%Y-%m-%d}{suffix}"
            download.save_as(out)
            return out
        finally:
            browser.close()


def run_portals() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for p in load_portals():
        try:
            path = download_spot_sheet(p)
            results[p["importer"]] = {"status": "ok", "file": path.name}
        except Exception as e:  # noqa: BLE001
            log.warning("portal %s failed: %s", p.get("importer"), e)
            results[p.get("importer", "?")] = {"status": "error", "message": str(e)}
    return results
