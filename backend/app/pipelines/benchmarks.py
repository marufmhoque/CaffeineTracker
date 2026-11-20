"""Tier 1: daily ICE Arabica 'C' / Robusta futures, origin FX rates, and ICO indicator prices.

Sources
- Yahoo Finance chart API (no key): KC=F (Arabica, US cents/lb), RC=F (Robusta, USD/tonne), XXX=X (FX, XXX per USD).
- open.er-api.com (no key) as a latest-rate fallback for FX symbols Yahoo doesn't cover.
- ICO indicator prices: the ICO publishes these without an API, so they are loaded from an optional
  CSV at data/ico_prices.csv (columns: date, composite, colombian_milds, other_milds, brazilian_naturals,
  robustas — US cents/lb, as the ICO publishes them).
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from sqlmodel import Session, select

from .. import config
from ..db import RunLogger
from ..http import get_with_retry, make_client
from ..models import MarketBenchmark
from ..units import cents_lb_to_usd_lb, usd_tonne_to_usd_lb

log = logging.getLogger(__name__)

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
ER_API_LATEST = "https://open.er-api.com/v6/latest/USD"
ARABICA_SYMBOL = os.getenv("ARABICA_YAHOO_SYMBOL", "KC=F")
# Yahoo does not list ICE London Robusta; set a symbol here if you have a working one, or drop
# daily settlements into data/ice_robusta.csv (columns: date, usd_tonne).
ROBUSTA_SYMBOL = os.getenv("ROBUSTA_YAHOO_SYMBOL", "")


def parse_yahoo_chart(payload: dict) -> list[tuple[date, float]]:
    """Extract (date, close) pairs from a Yahoo chart JSON payload, skipping null closes."""
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        err = (payload.get("chart") or {}).get("error")
        raise ValueError(f"empty Yahoo chart result: {err}")
    r = result[0]
    stamps = r.get("timestamp") or []
    quotes = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quotes.get("close") or []
    gmtoffset = (r.get("meta") or {}).get("gmtoffset", 0) or 0
    out: dict[date, float] = {}
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        d = datetime.fromtimestamp(ts + gmtoffset, tz=timezone.utc).date()
        out[d] = float(close)
    return sorted(out.items())


def fetch_yahoo_series(client: httpx.Client, symbol: str, range_: str) -> list[tuple[date, float]]:
    """Daily closes, excluding today's still-forming bar (intraday FX prints on thin pairs are noisy)."""
    resp = get_with_retry(
        client, YAHOO_CHART.format(symbol=symbol), params={"range": range_, "interval": "1d"}
    )
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    return [(d, v) for d, v in parse_yahoo_chart(resp.json()) if d < today]


def _upsert(session: Session, d: date) -> MarketBenchmark:
    row = session.exec(select(MarketBenchmark).where(MarketBenchmark.date == d)).first()
    if row is None:
        row = MarketBenchmark(date=d, fx_rates_json={}, ico_groups_json={})
    return row


def store_series(
    session: Session,
    arabica_cents: Iterable[tuple[date, float]] = (),
    robusta_usd_t: Iterable[tuple[date, float]] = (),
    fx: dict[str, Iterable[tuple[date, float]]] | None = None,
) -> int:
    """Merge price series into market_benchmarks rows (one row per date). Returns rows touched."""
    by_date: dict[date, dict] = {}
    for d, v in arabica_cents:
        by_date.setdefault(d, {})["arabica"] = round(cents_lb_to_usd_lb(v), 4)
    for d, v in robusta_usd_t:
        by_date.setdefault(d, {})["robusta"] = round(usd_tonne_to_usd_lb(v), 4)
    for ccy, series in (fx or {}).items():
        for d, v in series:
            by_date.setdefault(d, {}).setdefault("fx", {})[ccy] = v
    existing = {
        r.date: r
        for r in session.exec(
            select(MarketBenchmark).where(MarketBenchmark.date.in_(list(by_date)))
        ).all()
    } if by_date else {}
    for d, vals in by_date.items():
        row = existing.get(d) or MarketBenchmark(date=d, fx_rates_json={}, ico_groups_json={})
        if "arabica" in vals:
            row.ice_arabica_usd_lb = vals["arabica"]
        if "robusta" in vals:
            row.ice_robusta_usd_lb = vals["robusta"]
        if "fx" in vals:
            row.fx_rates_json = {**(row.fx_rates_json or {}), **vals["fx"]}
        session.add(row)
    session.commit()
    return len(by_date)


def load_ico_csv(session: Session, path=None) -> int:
    """Load ICO indicator prices (US cents/lb) from CSV into market_benchmarks."""
    path = path or config.ICO_PRICES_FILE
    if not path.exists():
        return 0
    df = pd.read_csv(path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    if "date" not in df.columns:
        raise ValueError("ico_prices.csv needs a 'date' column")
    groups = ["colombian_milds", "other_milds", "brazilian_naturals", "robustas"]
    n = 0
    for rec in df.to_dict("records"):
        d = pd.to_datetime(rec["date"]).date()
        row = _upsert(session, d)
        comp = rec.get("composite")
        if comp is not None and comp == comp:
            row.ico_composite_usd_lb = round(float(comp) / 100.0, 4)
        g = {k: round(float(rec[k]) / 100.0, 4) for k in groups if k in rec and rec[k] == rec[k]}
        if g:
            row.ico_groups_json = {**(row.ico_groups_json or {}), **g}
        session.add(row)
        n += 1
    session.commit()
    return n


def run(session: Session, *, backfill: bool | None = None, client: httpx.Client | None = None) -> dict:
    """Fetch Tier 1 data. Backfills 5y on an empty table, otherwise refreshes the last month."""
    logger = RunLogger(session, "benchmarks")
    if backfill is None:
        backfill = session.exec(select(MarketBenchmark.id).limit(1)).first() is None
    range_ = "5y" if backfill else "1mo"
    errors: list[str] = []
    own_client = client is None
    client = client or make_client()
    try:
        arabica = robusta = []
        try:
            arabica = fetch_yahoo_series(client, ARABICA_SYMBOL, range_)
        except Exception as e:  # noqa: BLE001
            errors.append(f"arabica {ARABICA_SYMBOL}: {e}")
        if ROBUSTA_SYMBOL:
            try:
                robusta = fetch_yahoo_series(client, ROBUSTA_SYMBOL, range_)
            except Exception as e:  # noqa: BLE001
                errors.append(f"robusta {ROBUSTA_SYMBOL}: {e}")
        robusta_csv = config.DATA_DIR / "ice_robusta.csv"
        if robusta_csv.exists():
            try:
                rdf = pd.read_csv(robusta_csv)
                rdf.columns = [c.strip().lower() for c in rdf.columns]
                robusta = list(robusta) + [
                    (pd.to_datetime(d).date(), float(v))
                    for d, v in zip(rdf["date"], rdf["usd_tonne"])
                    if v == v
                ]
            except Exception as e:  # noqa: BLE001
                errors.append(f"ice_robusta.csv: {e}")

        fx: dict[str, list[tuple[date, float]]] = {}
        missing: list[str] = []
        for ccy in config.FX_CURRENCIES:
            if ccy == "USD":
                continue
            try:
                series = fetch_yahoo_series(client, f"{ccy}=X", range_)
                if series:
                    fx[ccy] = series
                else:
                    missing.append(ccy)
            except Exception as e:  # noqa: BLE001
                log.info("Yahoo FX %s failed: %s", ccy, e)
                missing.append(ccy)
        if missing:
            try:
                resp = get_with_retry(client, ER_API_LATEST)
                data = resp.json()
                rates = data.get("rates", {})
                d = datetime.fromtimestamp(
                    data.get("time_last_update_unix", datetime.now(timezone.utc).timestamp()),
                    tz=timezone.utc,
                ).date()
                for ccy in list(missing):
                    if ccy in rates:
                        fx[ccy] = [(d, float(rates[ccy]))]
                        missing.remove(ccy)
            except Exception as e:  # noqa: BLE001
                errors.append(f"fx fallback: {e}")
        if missing:
            errors.append(f"no FX data for {','.join(missing)}")

        rows = store_series(session, arabica, robusta, fx)
        try:
            rows += load_ico_csv(session)
        except Exception as e:  # noqa: BLE001
            errors.append(f"ico csv: {e}")
    finally:
        if own_client:
            client.close()

    if rows == 0:
        status = "error"
    elif errors:
        status = "partial"
    else:
        status = "ok"
    logger.finish(status, rows, "; ".join(errors))
    return {"status": status, "rows": rows, "errors": errors}
