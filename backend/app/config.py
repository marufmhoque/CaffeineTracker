"""Runtime configuration. Values come from environment variables (or a `.env` file at the repo root)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DATA_DIR = Path(os.getenv("ORIGINPULSE_DATA_DIR", ROOT_DIR / "data"))
SEED_FILE = DATA_DIR / "countries_seed.json"
FREIGHT_RULES_FILE = DATA_DIR / "freight_rules.json"
SPOT_SHEETS_DIR = DATA_DIR / "spot_sheets"
ICO_PRICES_FILE = DATA_DIR / "ico_prices.csv"
PORTALS_FILE = DATA_DIR / "portals.json"

DATABASE_URL = os.getenv(
    "DATABASE_URL", f"sqlite:///{(DATA_DIR / 'originpulse.db').as_posix()}"
)

TIMEZONE = os.getenv("ORIGINPULSE_TZ", "America/New_York")
USER_AGENT = os.getenv(
    "ORIGINPULSE_USER_AGENT", "OriginPulse/0.1 (green coffee price tracker; low-volume)"
)
HTTP_TIMEOUT = float(os.getenv("ORIGINPULSE_HTTP_TIMEOUT", "30"))

ENABLE_SCHEDULER = _env_bool("ENABLE_SCHEDULER", True)
# Run Tier 1 / Tier 3 backfill in the background at startup when the tables are empty.
BOOTSTRAP_ON_START = _env_bool("BOOTSTRAP_ON_START", True)

# Tier 1 — FX currencies tracked against USD (PRD list + a few extra key origins).
FX_CURRENCIES = [
    c.strip().upper()
    for c in os.getenv(
        "FX_CURRENCIES", "BRL,COP,ETB,HNL,GTQ,KES,VND,IDR,PEN,MXN,UGX,INR,NIO,CRC,RWF,TZS"
    ).split(",")
    if c.strip()
]

# Tier 3 — UN Comtrade. Without a key the public preview endpoint is used (rate-limited, 500 rows/call).
COMTRADE_API_KEY = os.getenv("COMTRADE_API_KEY", "")
# Reporter (importing) countries as M49 codes. 842 = USA. Add e.g. 276 (Germany), 392 (Japan) for global view.
COMTRADE_REPORTERS = [
    int(x) for x in os.getenv("COMTRADE_REPORTERS", "842,276,392").split(",") if x.strip()
]
COMTRADE_BACKFILL_MONTHS = int(os.getenv("COMTRADE_BACKFILL_MONTHS", "18"))

# Tier 2 — importer scrapers to run (comma list of scraper keys, or "all").
# coffee_shrub is registered but off by default: its store doesn't serve the Shopify JSON catalog.
ENABLED_SCRAPERS = os.getenv("ENABLED_SCRAPERS", "royal_crown_jewels,bodhi_leaf,genuine_origin")

# Optional LLM extraction for spot-sheet PDFs whose tables can't be parsed.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")

# Email-forwarding webhook shared secret (required for /api/ingest/email to accept posts).
INGEST_WEBHOOK_TOKEN = os.getenv("INGEST_WEBHOOK_TOKEN", "")

# Alert delivery (all optional).
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_TO = os.getenv("SMTP_TO", "")

# Alert thresholds.
ALERT_ORIGIN_DROP_PCT = float(os.getenv("ALERT_ORIGIN_DROP_PCT", "5"))
ALERT_ORIGIN_DROP_DAYS = int(os.getenv("ALERT_ORIGIN_DROP_DAYS", "14"))
ALERT_MIN_SCORE = float(os.getenv("ALERT_MIN_SCORE", "87"))
ALERT_FX_WEAKEN_PCT = float(os.getenv("ALERT_FX_WEAKEN_PCT", "3"))

CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if o.strip()
]
