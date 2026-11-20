"""Read side for benchmark history ranges, predictive market signals and the origin detail drawer."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from . import analytics, costing
from .harvest import harvest_status
from .models import CertifiedStock, Country, CropEstimate, FuturesQuote, MarketBenchmark, WeatherSignal
from .pipelines import usda
from .pipelines.market import term_structure
from .species import species_share

HISTORY_RANGES = {"1m": 31, "6m": 183, "1y": 365, "3y": 365 * 3, "5y": 365 * 5 + 2, "max": None}
RISK_ORDER = {"Low": 0, "Moderate": 1, "Critical": 2}


def benchmark_history(session: Session, range_: str = "1y", today: date | None = None) -> dict[str, Any]:
    """ICE Arabica/Robusta daily history; ranges over 3 years are thinned to weekly points."""
    today = today or date.today()
    days = HISTORY_RANGES.get(range_, 365)
    q = select(MarketBenchmark.date, MarketBenchmark.ice_arabica_usd_lb, MarketBenchmark.ice_robusta_usd_lb).where(
        (MarketBenchmark.ice_arabica_usd_lb.is_not(None)) | (MarketBenchmark.ice_robusta_usd_lb.is_not(None))
    )
    if days:
        q = q.where(MarketBenchmark.date >= today - timedelta(days=days))
    rows = session.exec(q.order_by(MarketBenchmark.date)).all()
    weekly = days is None or days > 365 * 3
    points = []
    last_week = None
    for i, (d, a, r) in enumerate(rows):
        if weekly:
            wk = d.isocalendar()[:2]
            if wk == last_week and i != len(rows) - 1:
                points[-1] = {"date": d.isoformat(), "arabica": a, "robusta": r}
                continue
            last_week = wk
        points.append({"date": d.isoformat(), "arabica": a, "robusta": r})
    arab = [p["arabica"] for p in points if p["arabica"] is not None]
    rob = [p["robusta"] for p in points if p["robusta"] is not None]

    def stats(vals: list[float]) -> dict[str, Any] | None:
        if not vals:
            return None
        return {"first": vals[0], "last": vals[-1], "high": max(vals), "low": min(vals),
                "chg_pct": analytics.pct_change(vals[-1], vals[0])}

    earliest = session.exec(
        select(MarketBenchmark.date).where(MarketBenchmark.ice_arabica_usd_lb.is_not(None))
        .order_by(MarketBenchmark.date).limit(1)
    ).first()
    return {"range": range_, "interval": "weekly" if weekly else "daily", "points": points,
            "arabica": stats(arab), "robusta": stats(rob),
            "earliest": earliest.isoformat() if earliest else None}


# ---------------------------------------------------------------- signals

def forward_curve(session: Session) -> dict[str, Any]:
    latest = session.exec(select(FuturesQuote.date).order_by(FuturesQuote.date.desc()).limit(1)).first()
    if not latest:
        return {"date": None, "contracts": [], "structure": None, "month_ago": []}

    def curve_on(d: date) -> list[dict[str, Any]]:
        rows = session.exec(
            select(FuturesQuote).where(FuturesQuote.date == d, FuturesQuote.market == "arabica")
            .order_by(FuturesQuote.contract_month)
        ).all()
        return [{"symbol": r.symbol, "contract_month": r.contract_month, "usd_lb": r.usd_lb} for r in rows]

    cur = curve_on(latest)
    prior_date = session.exec(
        select(FuturesQuote.date).where(FuturesQuote.date <= latest - timedelta(days=25))
        .order_by(FuturesQuote.date.desc()).limit(1)
    ).first()
    prior = curve_on(prior_date) if prior_date else []

    def ser(rows):
        return [{**r, "contract_month": r["contract_month"].isoformat(),
                 "label": r["contract_month"].strftime("%b %y")} for r in rows]

    return {"date": latest.isoformat(), "contracts": ser(cur), "structure": term_structure(cur),
            "month_ago_date": prior_date.isoformat() if prior_date else None, "month_ago": ser(prior)}


def certified_stocks(session: Session, today: date | None = None) -> dict[str, Any]:
    """Certified bags vs ICE "C", with a supply-pinch flag.

    Pinch = stocks in the bottom 20% of their 5-year range AND down more than 10% over 90 days.
    """
    today = today or date.today()
    rows = session.exec(
        select(CertifiedStock).where(CertifiedStock.market == "arabica",
                                     CertifiedStock.date >= today - timedelta(days=365 * 5))
        .order_by(CertifiedStock.date)
    ).all()
    if not rows:
        return {"latest": None, "series": [], "pinch": None}
    ice = analytics.ice_series(session, rows[0].date - timedelta(days=10))
    series = [{"date": r.date.isoformat(), "bags": r.bags, "ice_usd_lb": analytics.asof(ice, r.date)} for r in rows]
    latest = rows[-1]
    bags = [r.bags for r in rows]
    lo, hi = min(bags), max(bags)
    pos = (latest.bags - lo) / (hi - lo) * 100 if hi > lo else 50.0
    ago = [r for r in rows if r.date <= latest.date - timedelta(days=90)]
    chg_90 = analytics.pct_change(latest.bags, ago[-1].bags) if ago else None
    pinch = pos <= 20 and chg_90 is not None and chg_90 < -10
    return {
        "latest": {"date": latest.date.isoformat(), "bags": latest.bags},
        "range_5y": {"low": lo, "high": hi, "position_pct": round(pos, 1)},
        "chg_90d_pct": chg_90,
        "pinch": pinch,
        "takeaway": (
            "Certified stocks are near a 5-year low and falling — historically a supply pinch that supports "
            "nearby prices." if pinch else
            "Exchange inventories are not signalling a pinch." if pos > 20 else
            "Certified stocks are low but not falling fast — watch for further drawdowns."
        ),
        "series": series,
    }


def weather_latest(session: Session, country: str | None = None) -> list[dict[str, Any]]:
    latest = session.exec(select(WeatherSignal.as_of).order_by(WeatherSignal.as_of.desc()).limit(1)).first()
    if not latest:
        return []
    q = select(WeatherSignal).where(WeatherSignal.as_of == latest)
    if country:
        q = q.where(WeatherSignal.country_iso == country)
    rows = session.exec(q).all()
    out = [{**r.model_dump(mode="json", exclude={"reasons_json", "id"}), "reasons": r.reasons_json} for r in rows]
    return sorted(out, key=lambda r: (-RISK_ORDER.get(r["risk"], 0), r["country_iso"], r["belt"]))


def _crop_rows(session: Session) -> dict[str, list[CropEstimate]]:
    by: dict[str, list[CropEstimate]] = defaultdict(list)
    for r in session.exec(select(CropEstimate)).all():
        by[r.country_iso].append(r)
    return by


def crops(session: Session, top: int = 10) -> dict[str, Any]:
    by = _crop_rows(session)
    origins = []
    for iso, rows in by.items():
        if iso == "WLD":
            continue
        cc = usda.crop_cycle(rows)
        if cc and cc["production_k_bags"] > 0:
            origins.append({"iso_code": iso, **cc})
    origins.sort(key=lambda r: -r["production_k_bags"])
    names = {c.iso_code: c.name for c in session.exec(select(Country)).all()}
    for o in origins:
        o["name"] = names.get(o["iso_code"], o["iso_code"])
    return {"world": usda.world_balance(by.get("WLD", [])), "origins": origins[:top]}


def signals(session: Session) -> dict[str, Any]:
    return {
        "curve": forward_curve(session),
        "weather": weather_latest(session),
        "certified_stocks": certified_stocks(session),
        "crops": crops(session),
    }


# ---------------------------------------------------------------- origin drawer

def country_detail(session: Session, iso: str, shrinkage: float | None = None) -> dict[str, Any] | None:
    c = session.get(Country, iso.upper())
    if not c:
        return None
    rules = costing.load_rules()
    shrink = rules["default_shrinkage"] if shrinkage is None else shrinkage
    matrix_row = next((r for r in analytics.country_matrix(session)["countries"] if r["iso_code"] == c.iso_code), None)
    crop_rows = _crop_rows(session).get(c.iso_code, [])
    cycle = usda.crop_cycle(crop_rows) if crop_rows else None
    if cycle and (cycle.get("arabica_k_bags") or cycle.get("robusta_k_bags")):
        a, r = cycle.get("arabica_k_bags") or 0, cycle.get("robusta_k_bags") or 0
        share = {"Arabica": round(a / (a + r) * 100), "Robusta": round(r / (a + r) * 100)} if a + r else {}
        share = {k: v for k, v in share.items() if v > 0}
        share_basis = f"USDA {cycle['market_year']}"
    else:
        share, share_basis = species_share(c.species_mix), "indicative"
    belts = weather_latest(session, c.iso_code)
    worst = max(belts, key=lambda b: RISK_ORDER.get(b["risk"], 0))["risk"] if belts else None

    lots = []
    for o in analytics.current_offers(session, country=c.iso_code):
        landed = o.atlanta_landed_usd_lb
        lots.append({
            "id": o.id, "importer": o.importer, "lot_name": o.lot_name, "producer": o.origin_region,
            "process": o.process, "variety": o.variety, "species": o.species, "sca_score": o.sca_score,
            "bag_weight_kg": o.bag_weight_kg, "bags_available": o.bags_available,
            "warehouse": o.warehouse_location, "is_southeast": o.is_southeast,
            "spot_usd_lb": o.spot_price_usd_lb, "landed_usd_lb": landed,
            "roasted_usd_lb": round(costing.roasted_cost(landed, shrink), 4) if landed else None,
            "raw_url": o.raw_url if (o.raw_url or "").startswith("http") else None,
        })
    return {
        "iso_code": c.iso_code, "name": c.name, "region": c.region, "species_mix": c.species_mix,
        "species_share": share, "species_share_basis": share_basis,
        "currency_code": c.currency_code, "ico_group": c.ico_group,
        "harvest_status": harvest_status(c.main_harvest_months, c.fly_harvest_months),
        "main_harvest_months": c.main_harvest_months, "fly_harvest_months": c.fly_harvest_months,
        "default_bag_kg": c.default_bag_kg,
        "typical_diff_usd_lb": [c.typical_diff_low, c.typical_diff_high],
        "prices": matrix_row,
        "weather_risk": worst, "weather": belts,
        "crop": cycle,
        "shrinkage": shrink,
        "lots": lots,
    }
