"""Market structure: ICE "C" forward curve (Yahoo contract symbols) and ICE certified stocks (CSV).

- Forward curve: Yahoo lists individual Arabica contract months as KC<month><yy>.NYB (e.g. KCH27.NYB).
  Arabica delivery months are Mar (H), May (K), Jul (N), Sep (U), Dec (Z).
- ICE certified stocks have no free API. Drop the daily exchange report into
  data/ice_certified_stocks.csv (columns: date, bags) or upload it on Data Sources.
- ICE Robusta (London) has no free feed either; data/ice_robusta.csv (date, usd_tonne) is loaded by the
  benchmarks pipeline.
"""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from sqlmodel import Session, select

from .. import config
from ..db import RunLogger
from ..http import make_client
from ..models import CertifiedStock, FuturesQuote
from ..units import cents_lb_to_usd_lb
from .benchmarks import fetch_yahoo_series

log = logging.getLogger(__name__)

ARABICA_MONTHS = {3: "H", 5: "K", 7: "N", 9: "U", 12: "Z"}
def cert_stocks_file() -> Path:
    return config.DATA_DIR / "ice_certified_stocks.csv"


def robusta_file() -> Path:
    return config.DATA_DIR / "ice_robusta.csv"


def arabica_contracts(today: date, n: int = 8) -> list[tuple[str, date]]:
    """The next `n` Arabica contract symbols, starting with the current/nearest delivery month."""
    out: list[tuple[str, date]] = []
    y, m = today.year, today.month
    while len(out) < n:
        if m in ARABICA_MONTHS:
            out.append((f"KC{ARABICA_MONTHS[m]}{y % 100:02d}.NYB", date(y, m, 1)))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def fetch_curve(client: httpx.Client, today: date | None = None) -> tuple[list[FuturesQuote], list[str]]:
    today = today or date.today()
    quotes, errors = [], []
    for symbol, month in arabica_contracts(today):
        try:
            series = fetch_yahoo_series(client, symbol, "5d")
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:  # 404 = contract month not listed yet
                errors.append(f"{symbol}: HTTP {e.response.status_code}")
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"{symbol}: {e}")
            continue
        if not series:
            continue
        d, cents = series[-1]
        if d < today - timedelta(days=7):
            continue  # expired/illiquid contract
        quotes.append(FuturesQuote(date=d, market="arabica", symbol=symbol, contract_month=month,
                                   usd_lb=round(cents_lb_to_usd_lb(cents), 4)))
    return quotes, errors


def store_quotes(session: Session, quotes: list[FuturesQuote]) -> int:
    for q in quotes:
        row = session.exec(
            select(FuturesQuote).where(FuturesQuote.date == q.date, FuturesQuote.symbol == q.symbol)
        ).first()
        if row:
            row.usd_lb = q.usd_lb
            session.add(row)
        else:
            session.add(q)
    session.commit()
    return len(quotes)


def term_structure(quotes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Classify a curve (sorted by contract month) as contango or backwardation, with a takeaway.

    Slope = (furthest - nearest) / nearest. Within ±1.5% the curve is called flat.
    """
    if len(quotes) < 2:
        return None
    front, back = quotes[0], quotes[-1]
    months = max(1, (back["contract_month"].year - front["contract_month"].year) * 12
                 + back["contract_month"].month - front["contract_month"].month)
    slope_pct = (back["usd_lb"] - front["usd_lb"]) / front["usd_lb"] * 100
    annual = slope_pct * 12 / months
    if slope_pct > 1.5:
        state = "Contango"
        takeaway = ("Later deliveries cost more than nearby coffee: the market sees adequate supply now and is "
                    "paying for storage. No rush to book forward; spot buying is competitive.")
    elif slope_pct < -1.5:
        state = "Backwardation"
        takeaway = ("Nearby coffee costs more than later deliveries: physical supply is tight today and the market "
                    "expects relief. Cover only near-term needs at spot; forward contracts are cheaper.")
    else:
        state = "Flat"
        takeaway = "Nearby and deferred prices are close: no strong supply signal from the curve."
    return {"state": state, "slope_pct": round(slope_pct, 2), "annualized_pct": round(annual, 2),
            "months": months, "takeaway": takeaway}


# ---------------------------------------------------------------- CSV-fed series

def parse_cert_stocks_csv(data: bytes | str | Path) -> list[tuple[date, int]]:
    """Rows of (date, bags). Accepts columns date + bags (or certified/total/stocks)."""
    if isinstance(data, Path):
        df = pd.read_csv(data)
    else:
        df = pd.read_csv(io.BytesIO(data) if isinstance(data, bytes) else io.StringIO(data))
    df.columns = [str(c).strip().lower() for c in df.columns]
    val = next((c for c in ("bags", "certified", "certified_bags", "total", "stocks") if c in df.columns), None)
    if "date" not in df.columns or val is None:
        raise ValueError("certified stocks CSV needs columns: date, bags")
    out = []
    for d, v in zip(df["date"], df[val]):
        if v == v and str(v).strip():
            out.append((pd.to_datetime(d).date(), int(float(str(v).replace(",", "")))))
    return out


def parse_robusta_csv(data: bytes | str | Path) -> list[tuple[date, float]]:
    """Rows of (date, USD/tonne)."""
    if isinstance(data, Path):
        df = pd.read_csv(data)
    else:
        df = pd.read_csv(io.BytesIO(data) if isinstance(data, bytes) else io.StringIO(data))
    df.columns = [str(c).strip().lower() for c in df.columns]
    val = next((c for c in ("usd_tonne", "settle", "close", "price") if c in df.columns), None)
    if "date" not in df.columns or val is None:
        raise ValueError("Robusta CSV needs columns: date, usd_tonne")
    return [(pd.to_datetime(d).date(), float(str(v).replace(",", "")))
            for d, v in zip(df["date"], df[val]) if v == v and str(v).strip()]


def store_cert_stocks(session: Session, rows: list[tuple[date, int]], market: str = "arabica") -> int:
    existing = {r.date: r for r in session.exec(select(CertifiedStock).where(CertifiedStock.market == market)).all()}
    for d, bags in rows:
        row = existing.get(d) or CertifiedStock(date=d, market=market, bags=bags)
        row.bags = bags
        session.add(row)
    session.commit()
    return len(rows)


def import_csv(session: Session, kind: str, data: bytes) -> dict[str, Any]:
    """Store an uploaded CSV and load it. kind = 'certified_stocks' | 'robusta'."""
    from .benchmarks import store_series

    if kind == "certified_stocks":
        rows = parse_cert_stocks_csv(data)
        path = cert_stocks_file()
        n = store_cert_stocks(session, rows)
    elif kind == "robusta":
        rows = parse_robusta_csv(data)
        path = robusta_file()
        n = store_series(session, robusta_usd_t=rows)
    else:
        raise ValueError("kind must be certified_stocks or robusta")
    try:  # keep the file so a rebuild of the database reloads it
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        pass
    return {"status": "ok", "kind": kind, "rows": n}


def run(session: Session, client: httpx.Client | None = None, today: date | None = None) -> dict[str, Any]:
    logger = RunLogger(session, "market")
    errors: list[str] = []
    rows = 0
    own = client is None
    client = client or make_client()
    try:
        quotes, errs = fetch_curve(client, today)
        errors += errs
        rows += store_quotes(session, quotes)
    finally:
        if own:
            client.close()
    if cert_stocks_file().exists():
        try:
            rows += store_cert_stocks(session, parse_cert_stocks_csv(cert_stocks_file()))
        except Exception as e:  # noqa: BLE001
            errors.append(f"certified stocks csv: {e}")
    status = "error" if rows == 0 else ("partial" if errors else "ok")
    logger.finish(status, rows, "; ".join(errors))
    return {"status": status, "rows": rows, "errors": errors}
