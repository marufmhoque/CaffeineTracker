"""Tier 3: import unit values for green coffee (HS 0901.11, not roasted, not decaffeinated).

Unit value = customs value (USD) / net weight (kg), converted to USD/lb. This gives every producing
country that ships to a tracked market a baseline price, including small origins (Yemen, Burundi, ...).

Sources, in order of preference:
1. US Census International Trade API (monthly, reported quantities). Needs a free CENSUS_API_KEY.
2. UN Comtrade monthly for the configured reporters (default USA, Germany, Japan). Rows whose net
   weight Comtrade *estimated* from value are dropped — e.g. recent US monthly data is all estimated,
   which would otherwise yield a flat, meaningless unit value.
3. UN Comtrade annual for the same reporters (fallback baseline for origins with no monthly data).

Without COMTRADE_API_KEY the public preview endpoint is used (rate-limited, max 500 rows per call).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date

import httpx
from sqlmodel import Session, select

from .. import config
from ..db import RunLogger
from ..geo import resolve_census_country
from ..http import get_with_retry, make_client
from ..models import Country, TradeValuation
from ..units import LB_PER_KG

log = logging.getLogger(__name__)

COMTRADE_PREVIEW = "https://comtradeapi.un.org/public/v1/preview/C/{freq}/HS"
COMTRADE_DATA = "https://comtradeapi.un.org/data/v1/get/C/{freq}/HS"
CENSUS_URL = "https://api.census.gov/data/timeseries/intltrade/imports/hs"
CENSUS_API_KEY = os.getenv("CENSUS_API_KEY", "")
HS_CODE = "090111"
REPORTER_ISO = {842: "USA", 276: "DEU", 392: "JPN", 380: "ITA", 56: "BEL", 528: "NLD", 410: "KOR", 124: "CAN"}
# Comtrade partner codes that differ from M49 (None = not a coffee origin / aggregate).
PARTNER_CODE_ALIASES: dict[int, str | None] = {490: "TWN", 699: "IND", 0: None, 251: None, 757: None, 842: None}
KG_UNIT_CODES = {8}  # Comtrade qtyUnitCode 8 = kg


def month_list(end: date, n: int) -> list[date]:
    """The n months ending at (and including) `end`, oldest first."""
    months = []
    y, m = end.year, end.month
    for _ in range(n):
        months.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(months)


def _unit_value(value: float, kg: float) -> float:
    return round(float(value) / (float(kg) * LB_PER_KG), 4)


def parse_comtrade_rows(
    rows: list[dict], m49_to_iso: dict[int, str], *, allow_estimated: bool = False
) -> list[dict]:
    """Turn Comtrade records into trade valuation dicts."""
    out = []
    for r in rows:
        partner = r.get("partnerCode")
        if partner is None:
            continue
        partner = int(partner)
        if partner in PARTNER_CODE_ALIASES:
            iso = PARTNER_CODE_ALIASES[partner]
        else:
            iso = m49_to_iso.get(partner)
        if iso is None:
            continue
        if r.get("isNetWgtEstimated") and not allow_estimated:
            continue
        value = r.get("primaryValue") or r.get("cifvalue") or r.get("fobvalue")
        kg = r.get("netWgt")
        if not kg and r.get("qtyUnitCode") in KG_UNIT_CODES and not r.get("isQtyEstimated"):
            kg = r.get("qty")
        if not value or not kg or kg <= 0 or value <= 0:
            continue
        period = str(r.get("period") or "")
        freq = r.get("freqCode") or ("A" if len(period) == 4 else "M")
        if freq == "A":
            pm = date(int(period[:4]), 1, 1)
        else:
            pm = date(int(period[:4]), int(period[4:6]), 1)
        out.append(
            {
                "period_month": pm,
                "freq": freq,
                "country_iso": iso,
                "import_volume_kg": float(kg),
                "import_value_usd": float(value),
                "unit_price_usd_lb": _unit_value(value, kg),
                "source": "un_comtrade",
            }
        )
    return out


def parse_census_rows(table: list[list[str]], period: date) -> list[dict]:
    """Census returns [header, *rows]. Sum HS10 lines under 090111 per country."""
    if not table or len(table) < 2:
        return []
    header = table[0]
    idx = {h: i for i, h in enumerate(header)}
    agg: dict[str, list[float]] = {}
    for row in table[1:]:
        name = row[idx["CTY_NAME"]]
        code = row[idx["CTY_CODE"]]
        if code.startswith("0") or code.startswith("-") or "TOTAL" in name.upper():
            continue  # world/region aggregates use codes like 0000, 0003, -
        unit = row[idx["UNIT_QY1"]] if "UNIT_QY1" in idx else "KG"
        if unit and unit.upper() not in ("KG", "X"):
            continue
        iso = resolve_census_country(name)
        if not iso:
            continue
        try:
            val = float(row[idx["GEN_VAL_MO"]])
            kg = float(row[idx["GEN_QY1_MO"]])
        except (TypeError, ValueError):
            continue
        a = agg.setdefault(iso, [0.0, 0.0])
        a[0] += val
        a[1] += kg
    return [
        {
            "period_month": period,
            "freq": "M",
            "country_iso": iso,
            "import_volume_kg": kg,
            "import_value_usd": val,
            "unit_price_usd_lb": _unit_value(val, kg),
            "source": "us_census",
        }
        for iso, (val, kg) in agg.items()
        if val > 0 and kg > 0
    ]


def fetch_comtrade(client: httpx.Client, reporter: int, period: str, freq: str) -> list[dict]:
    params = {
        "reporterCode": reporter,
        "period": period,
        "cmdCode": HS_CODE,
        "flowCode": "M",
        "customsCode": "C00",
        "motCode": 0,
        "partner2Code": 0,
    }
    if config.COMTRADE_API_KEY:
        resp = get_with_retry(
            client,
            COMTRADE_DATA.format(freq=freq),
            params=params,
            headers={"Ocp-Apim-Subscription-Key": config.COMTRADE_API_KEY},
        )
    else:
        resp = get_with_retry(client, COMTRADE_PREVIEW.format(freq=freq), params=params, retries=3, max_wait=60)
    payload = resp.json()
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload.get("data") or []


def fetch_census(client: httpx.Client, period: date) -> list[list[str]]:
    params = {
        "get": "CTY_CODE,CTY_NAME,I_COMMODITY,GEN_VAL_MO,GEN_QY1_MO,UNIT_QY1",
        "time": period.strftime("%Y-%m"),
        "COMM_LVL": "HS10",
        "I_COMMODITY": HS_CODE + "*",
        "key": CENSUS_API_KEY,
    }
    resp = get_with_retry(client, CENSUS_URL, params=params)
    if resp.status_code == 204 or not resp.text.strip():
        return []
    if "json" not in resp.headers.get("content-type", ""):
        raise RuntimeError(f"Census API returned non-JSON ({resp.headers.get('content-type')})")
    return resp.json()


def store_valuations(session: Session, vals: list[dict], reporter: str) -> int:
    n = 0
    for v in vals:
        row = session.exec(
            select(TradeValuation).where(
                TradeValuation.period_month == v["period_month"],
                TradeValuation.country_iso == v["country_iso"],
                TradeValuation.reporter == reporter,
                TradeValuation.hs_code == HS_CODE,
                TradeValuation.freq == v["freq"],
            )
        ).first()
        if row is None:
            row = TradeValuation(reporter=reporter, hs_code=HS_CODE, **v)
        else:
            for k, val in v.items():
                setattr(row, k, val)
        session.add(row)
        n += 1
    session.commit()
    return n


def run(
    session: Session,
    *,
    months: int | None = None,
    client: httpx.Client | None = None,
    today: date | None = None,
    pause_s: float = 1.5,
    annual_years: int = 3,
) -> dict:
    """Fetch recent months (default: full backfill on an empty table, else last 6 to catch revisions)."""
    logger = RunLogger(session, "trade")
    m49_to_iso = {c.m49: c.iso_code for c in session.exec(select(Country)).all()}
    empty = session.exec(select(TradeValuation.id).limit(1)).first() is None
    if months is None:
        months = config.COMTRADE_BACKFILL_MONTHS if empty else 6
    today = today or date.today()
    this_month = date(today.year, today.month, 1)
    periods = month_list(this_month, months + 1)[:-1]  # exclude the current, incomplete month

    errors: list[str] = []
    total = 0
    unpublished = 0
    own_client = client is None
    client = client or make_client()

    def pause():
        if pause_s:
            time.sleep(pause_s)

    try:
        census_ok = False
        if CENSUS_API_KEY:
            for pm in reversed(periods):
                try:
                    vals = parse_census_rows(fetch_census(client, pm), pm)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"census {pm:%Y-%m}: {e}")
                    continue
                if vals:
                    census_ok = True
                    total += store_valuations(session, vals, "USA")
                else:
                    unpublished += 1

        for reporter in config.COMTRADE_REPORTERS:
            rname = REPORTER_ISO.get(reporter, str(reporter))
            if reporter == 842 and census_ok:
                continue  # Census already gives real US monthly data
            rate_limited = False
            for pm in reversed(periods):
                try:
                    rows = fetch_comtrade(client, reporter, pm.strftime("%Y%m"), "M")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"comtrade {rname} {pm:%Y-%m}: {e}")
                    rate_limited = "429" in str(e)
                    pause()
                    if rate_limited:
                        break
                    continue
                pause()
                vals = parse_comtrade_rows(rows, m49_to_iso)
                if not vals:
                    unpublished += 1
                    continue
                total += store_valuations(session, vals, rname)
            if rate_limited:
                continue
            # Annual fallback: last N complete years.
            for year in range(today.year - annual_years, today.year):
                try:
                    rows = fetch_comtrade(client, reporter, str(year), "A")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"comtrade {rname} {year} annual: {e}")
                    pause()
                    continue
                pause()
                vals = parse_comtrade_rows(rows, m49_to_iso)
                total += store_valuations(session, vals, rname)
    finally:
        if own_client:
            client.close()

    status = "ok" if total and not errors else ("partial" if total else "error")
    msg = "; ".join(errors[:10])
    if unpublished:
        msg = (msg + "; " if msg else "") + f"{unpublished} reporter-month(s) empty or not yet published"
    logger.finish(status, total, msg)
    return {"status": status, "rows": total, "errors": errors, "unpublished": unpublished}
