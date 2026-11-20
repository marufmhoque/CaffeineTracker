"""USDA FAS PSD coffee balance sheets (no key): production by species, consumption, stocks.

Source: https://apps.fas.usda.gov/psdonline/downloads/psd_coffee_csv.zip (thousand 60 kg bags,
marketing years; the latest year is USDA's projection).

Derived signals (heuristics, labelled as such in the UI):
- Biennial cycle: Arabica trees alternate heavy ("on") and light ("off") crops. A country's latest Arabica
  (or total) crop above the previous year's is called an on-year, below it an off-year.
- Surplus/deficit: world production minus world domestic consumption, as % of consumption.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date
from typing import Any

import httpx
import pandas as pd
from sqlmodel import Session, select

from ..db import RunLogger
from ..http import get_with_retry, make_client
from ..models import Country, CropEstimate

log = logging.getLogger(__name__)

PSD_URL = "https://apps.fas.usda.gov/psdonline/downloads/psd_coffee_csv.zip"
YEARS_KEPT = 12
ATTRIBUTES = {
    "Production": "production",
    "Arabica Production": "arabica_production",
    "Robusta Production": "robusta_production",
    "Domestic Consumption": "domestic_consumption",
    "Exports": "exports",
    "Ending Stocks": "ending_stocks",
}
# USDA country names that differ from the seed's.
NAME_ALIASES = {
    "China": "CHN", "Congo (Kinshasa)": "COD", "Cote d'Ivoire": "CIV", "Congo (Brazzaville)": "COG",
    "Laos": "LAO", "Burma": "MMR", "Vietnam": "VNM", "Tanzania": "TZA", "Bolivia": "BOL",
    "Venezuela": "VEN", "Yemen": "YEM", "Timor-Leste": "TLS",
}


def parse_psd(raw: bytes, name_to_iso: dict[str, str], min_year: int) -> list[CropEstimate]:
    """Rows from the PSD zip (or a bare CSV) for known producers plus a WLD (world) total."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            raw = z.read(name)
    df = pd.read_csv(io.BytesIO(raw))
    df = df[df["Attribute_Description"].isin(ATTRIBUTES) & (df["Market_Year"] >= min_year)]
    out: list[CropEstimate] = []
    world: dict[tuple[int, str], float] = {}
    for rec in df.itertuples(index=False):
        attr = ATTRIBUTES[rec.Attribute_Description]
        year = int(rec.Market_Year)
        value = float(rec.Value)
        if attr in ("production", "domestic_consumption", "ending_stocks", "arabica_production",
                    "robusta_production"):
            world[(year, attr)] = world.get((year, attr), 0.0) + value
        iso = NAME_ALIASES.get(rec.Country_Name) or name_to_iso.get(rec.Country_Name)
        if iso:
            out.append(CropEstimate(country_iso=iso, market_year=year, attribute=attr, value_k_bags=value))
    out += [CropEstimate(country_iso="WLD", market_year=y, attribute=a, value_k_bags=v) for (y, a), v in world.items()]
    return out


def store(session: Session, rows: list[CropEstimate]) -> int:
    existing = {(r.country_iso, r.market_year, r.attribute): r for r in session.exec(select(CropEstimate)).all()}
    for r in rows:
        cur = existing.get((r.country_iso, r.market_year, r.attribute))
        if cur:
            cur.value_k_bags = r.value_k_bags
            session.add(cur)
        else:
            session.add(r)
    session.commit()
    return len(rows)


def run(session: Session, client: httpx.Client | None = None) -> dict[str, Any]:
    logger = RunLogger(session, "usda")
    own = client is None
    client = client or make_client()
    try:
        raw = get_with_retry(client, PSD_URL).content
        name_to_iso = {c.name: c.iso_code for c in session.exec(select(Country)).all()}
        rows = parse_psd(raw, name_to_iso, date.today().year - YEARS_KEPT)
        n = store(session, rows)
        logger.finish("ok" if n else "error", n, "" if n else "no rows parsed")
        return {"status": "ok" if n else "error", "rows": n}
    except Exception as e:  # noqa: BLE001
        logger.finish("error", 0, str(e))
        return {"status": "error", "rows": 0, "errors": [str(e)]}
    finally:
        if own:
            client.close()


# ---------------------------------------------------------------- read side

def _series(rows: list[CropEstimate], attr: str) -> dict[int, float]:
    return {r.market_year: r.value_k_bags for r in rows if r.attribute == attr}


def crop_cycle(rows: list[CropEstimate]) -> dict[str, Any] | None:
    """Crop-cycle summary for one country's rows (latest marketing year vs prior and 5-year average)."""
    arabica = {y: v for y, v in _series(rows, "arabica_production").items() if v > 0}
    total = {y: v for y, v in _series(rows, "production").items() if v > 0}
    if not total:
        return None
    latest = max(total)
    prev = total.get(latest - 1)
    hist = [total[y] for y in range(latest - 5, latest) if y in total]
    avg5 = sum(hist) / len(hist) if hist else None
    # The biennial on/off pattern is an Arabica trait: only label Arabica-majority origins.
    arabica_major = latest in arabica and arabica[latest] >= 0.5 * total[latest]
    basis = arabica if arabica_major and (latest - 1) in arabica else total
    on_off = None
    if arabica_major and latest in basis and (latest - 1) in basis and basis[latest - 1]:
        change = (basis[latest] - basis[latest - 1]) / basis[latest - 1] * 100
        if abs(change) >= 3:
            on_off = "On-year" if change > 0 else "Off-year"
    return {
        "market_year": f"{latest}/{str(latest + 1)[-2:]}",
        "production_k_bags": total[latest],
        "arabica_k_bags": arabica.get(latest),
        "robusta_k_bags": _series(rows, "robusta_production").get(latest),
        "yoy_pct": round((total[latest] - prev) / prev * 100, 1) if prev else None,
        "vs_5y_avg_pct": round((total[latest] - avg5) / avg5 * 100, 1) if avg5 else None,
        "cycle": on_off,
        "cycle_basis": "Arabica crop" if basis is arabica else "total crop",
    }


def world_balance(rows: list[CropEstimate]) -> dict[str, Any] | None:
    prod = _series(rows, "production")
    cons = _series(rows, "domestic_consumption")
    years = sorted(set(prod) & set(cons))
    if not years:
        return None
    y = years[-1]
    surplus = prod[y] - cons[y]
    out = {
        "market_year": f"{y}/{str(y + 1)[-2:]}",
        "production_k_bags": prod[y],
        "consumption_k_bags": cons[y],
        "surplus_k_bags": round(surplus, 0),
        "surplus_pct": round(surplus / cons[y] * 100, 1) if cons[y] else None,
        "ending_stocks_k_bags": _series(rows, "ending_stocks").get(y),
        "history": [{"year": yy, "production": prod[yy], "consumption": cons[yy]} for yy in years[-8:]],
    }
    for sp in ("arabica", "robusta"):
        s = _series(rows, f"{sp}_production")
        if y in s and (y - 1) in s and s[y - 1]:
            out[f"{sp}_yoy_pct"] = round((s[y] - s[y - 1]) / s[y - 1] * 100, 1)
    return out
