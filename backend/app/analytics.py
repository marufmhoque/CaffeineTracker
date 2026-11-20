"""Read-side analytics: current offers, as-of spot averages, country matrix, benchmark summary, history.

"Current" spot offers = each importer's most recent batch, if it is no older than STALE_DAYS.
Spot averages "as of" a day use the same rule evaluated at that day, so a weekly spot sheet still counts
for the days between sheets.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, func
from sqlmodel import Session, select

from . import config
from .harvest import harvest_status
from .models import Country, MarketBenchmark, SpotOffer, TradeValuation

STALE_DAYS = 21


# ---------------------------------------------------------------- benchmarks

def ice_series(session: Session, start: date | None = None) -> list[tuple[date, float]]:
    q = select(MarketBenchmark.date, MarketBenchmark.ice_arabica_usd_lb).where(
        MarketBenchmark.ice_arabica_usd_lb.is_not(None)
    )
    if start:
        q = q.where(MarketBenchmark.date >= start)
    return [(d, v) for d, v in session.exec(q.order_by(MarketBenchmark.date)).all()]


def asof(series: list[tuple[date, float]], day: date) -> float | None:
    """Latest value on or before `day` (series sorted ascending)."""
    lo, hi, ans = 0, len(series) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= day:
            ans = series[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def fx_series(session: Session, start: date | None = None) -> dict[str, list[tuple[date, float]]]:
    q = select(MarketBenchmark.date, MarketBenchmark.fx_rates_json)
    if start:
        q = q.where(MarketBenchmark.date >= start)
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for d, fx in session.exec(q.order_by(MarketBenchmark.date)).all():
        for ccy, v in (fx or {}).items():
            if v:
                out[ccy].append((d, float(v)))
    return out


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / old * 100, 2)


def benchmark_summary(session: Session, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    series = ice_series(session, today - timedelta(days=400))
    latest = series[-1] if series else None
    last_30 = [v for d, v in series if d > today - timedelta(days=30)]
    last_52w = [v for d, v in series if d > today - timedelta(days=365)]
    rob = session.exec(
        select(MarketBenchmark.date, MarketBenchmark.ice_robusta_usd_lb)
        .where(MarketBenchmark.ice_robusta_usd_lb.is_not(None))
        .order_by(MarketBenchmark.date.desc())
    ).first()
    ico = session.exec(
        select(MarketBenchmark)
        .where(MarketBenchmark.ico_composite_usd_lb.is_not(None))
        .order_by(MarketBenchmark.date.desc())
    ).first()

    fx = fx_series(session, today - timedelta(days=400))
    fx_out = []
    for ccy, s in sorted(fx.items()):
        cur = s[-1]
        fx_out.append({
            "currency": ccy,
            "date": cur[0].isoformat(),
            "per_usd": cur[1],
            "chg_7d_pct": pct_change(cur[1], asof(s, cur[0] - timedelta(days=7))),
            "chg_30d_pct": pct_change(cur[1], asof(s, cur[0] - timedelta(days=30))),
            "chg_1y_pct": pct_change(cur[1], asof(s, cur[0] - timedelta(days=365))),
        })

    return {
        "ice_arabica": {
            "date": latest[0].isoformat() if latest else None,
            "usd_lb": latest[1] if latest else None,
            "ma30_usd_lb": round(mean(last_30), 4) if last_30 else None,
            "high_52w": max(last_52w) if last_52w else None,
            "low_52w": min(last_52w) if last_52w else None,
            "chg_1d_pct": pct_change(latest[1], series[-2][1]) if len(series) > 1 else None,
            "chg_30d_pct": pct_change(latest[1], asof(series, latest[0] - timedelta(days=30))) if latest else None,
            "chg_1y_pct": pct_change(latest[1], asof(series, latest[0] - timedelta(days=365))) if latest else None,
        },
        "ice_robusta": {"date": rob[0].isoformat(), "usd_lb": rob[1]} if rob else None,
        "ico": {
            "date": ico.date.isoformat(),
            "composite_usd_lb": ico.ico_composite_usd_lb,
            "groups": ico.ico_groups_json,
        } if ico else None,
        "fx": fx_out,
    }


# ---------------------------------------------------------------- spot offers

def _day(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.date()


def latest_batches(session: Session, as_of: datetime | None = None) -> list[str]:
    """Batch ids of each importer's most recent non-stale batch (optionally as of a past moment)."""
    as_of = as_of or datetime.now(timezone.utc)
    q = select(SpotOffer.importer, func.max(SpotOffer.scraped_at)).where(SpotOffer.scraped_at <= as_of)
    q = q.where(SpotOffer.scraped_at >= as_of - timedelta(days=STALE_DAYS)).group_by(SpotOffer.importer)
    pairs = session.exec(q).all()
    if not pairs:
        return []
    ids: set[str] = set()
    for importer, ts in pairs:
        for b in session.exec(
            select(SpotOffer.batch_id).where(SpotOffer.importer == importer, SpotOffer.scraped_at == ts).distinct()
        ).all():
            ids.add(b)
    return list(ids)


def current_offers(
    session: Session,
    *,
    country: str | None = None,
    process: str | None = None,
    min_score: float | None = None,
    southeast_only: bool = False,
    importer: str | None = None,
) -> list[SpotOffer]:
    batches = latest_batches(session)
    if not batches:
        return []
    q = select(SpotOffer).where(SpotOffer.batch_id.in_(batches))
    if country:
        q = q.where(SpotOffer.country_iso == country.upper())
    if process:
        q = q.where(SpotOffer.process == process)
    if min_score is not None:
        q = q.where(SpotOffer.sca_score >= min_score)
    if southeast_only:
        q = q.where(SpotOffer.is_southeast.is_(True))
    if importer:
        q = q.where(SpotOffer.importer == importer)
    return list(session.exec(q.order_by(SpotOffer.atlanta_landed_usd_lb)).all())


# Lots packed at or above this size are full export bags / bulk (the roaster's buying unit); smaller
# boxes (e.g. 22 lb Crown Jewels) carry a small-lot premium and are only used when no bags are offered.
BULK_MIN_KG = 20.0


def is_bulk(bag_kg: float | None) -> bool:
    return bag_kg is None or bag_kg >= BULK_MIN_KG


def _batch_aggregates(session: Session, start: datetime, country: str | None = None, process: str | None = None):
    """Per-batch, per-country(/process, pack size) price sums since `start` (plus STALE_DAYS lookback)."""
    bulk = case((SpotOffer.bag_weight_kg < BULK_MIN_KG, False), else_=True)
    q = select(
        SpotOffer.batch_id, SpotOffer.importer, func.max(SpotOffer.scraped_at),
        SpotOffer.country_iso, SpotOffer.process, bulk,
        func.count(SpotOffer.id), func.sum(SpotOffer.spot_price_usd_lb),
    ).where(SpotOffer.scraped_at >= start - timedelta(days=STALE_DAYS), SpotOffer.country_iso.is_not(None))
    if country:
        q = q.where(SpotOffer.country_iso == country)
    if process:
        q = q.where(SpotOffer.process == process)
    q = q.group_by(SpotOffer.batch_id, SpotOffer.importer, SpotOffer.country_iso, SpotOffer.process, bulk)
    rows = []
    for batch, imp, ts, iso, proc, is_bulk_, n, total in session.exec(q).all():
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        rows.append((batch, imp, ts, iso, proc, bool(is_bulk_), n, total))
    return rows


class SpotIndex:
    """As-of average spot price per country (optionally per process) from batch aggregates.

    Averages use full-bag lots when a country has any; otherwise small packs (basis reported).
    """

    def __init__(self, rows):
        # importer -> sorted list of (ts, batch)
        self.by_importer: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
        self.batch_rows: dict[str, list[tuple[str, str | None, bool, int, float]]] = defaultdict(list)
        seen = set()
        for batch, imp, ts, iso, proc, bulk, n, total in rows:
            if (imp, batch) not in seen:
                self.by_importer[imp].append((ts, batch))
                seen.add((imp, batch))
            self.batch_rows[batch].append((iso, proc, bulk, n, total))
        for v in self.by_importer.values():
            v.sort()

    def batches_asof(self, moment: datetime) -> list[str]:
        out = []
        for lst in self.by_importer.values():
            pick = None
            for ts, b in lst:
                if ts <= moment:
                    pick = (ts, b)
                else:
                    break
            if pick and pick[0] >= moment - timedelta(days=STALE_DAYS):
                out.append(pick[1])
        return out

    def avg(
        self, moment: datetime, country: str | None = None, process: str | None = None
    ) -> dict[str, tuple[float, int, str]]:
        """country -> (avg price, n offers, basis 'bags'|'small packs') as of `moment`."""
        acc: dict[tuple[str, bool], list[float]] = defaultdict(lambda: [0.0, 0])
        for b in self.batches_asof(moment):
            for iso, proc, bulk, n, total in self.batch_rows[b]:
                if country and iso != country:
                    continue
                if process and proc != process:
                    continue
                acc[(iso, bulk)][0] += total
                acc[(iso, bulk)][1] += n
        out: dict[str, tuple[float, int, str]] = {}
        for iso in {k[0] for k in acc}:
            v = acc.get((iso, True))
            basis = "bags"
            if not v or not v[1]:
                v, basis = acc[(iso, False)], "small packs"
            if v[1]:
                out[iso] = (v[0] / v[1], int(v[1]), basis)
        return out


def _eod(d: date) -> datetime:
    """End of day `d` in the app's local timezone (America/New_York), as an aware UTC datetime."""
    local = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=ZoneInfo(config.TIMEZONE))
    return local.astimezone(timezone.utc)


def spot_index(session: Session, start: date, country: str | None = None, process: str | None = None) -> SpotIndex:
    return SpotIndex(_batch_aggregates(session, _eod(start) - timedelta(days=1), country, process))


# ---------------------------------------------------------------- trade

TRADE_WINDOW_MONTHS = 3  # rolling window that smooths lumpy monthly shipments
MONTHLY_MAX_AGE_DAYS = 270  # older monthly data -> fall back to annual


def _shift_month(d: date, months: int) -> date:
    y, m = d.year, d.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return date(y, m, 1)


def _wavg(group: list[TradeValuation]) -> tuple[float | None, float]:
    kg = sum(g.import_volume_kg for g in group)
    if not kg:
        return None, 0.0
    return round(sum(g.import_value_usd for g in group) / (kg * 2.2046226218), 4), kg


def latest_trade(session: Session, today: date | None = None) -> dict[str, dict[str, Any]]:
    """Per country customs unit value from ONE primary importing market, so changes compare like with like.

    Primary market = the reporter that imported the most from that origin over the last 12 months of
    monthly data. Its latest TRADE_WINDOW_MONTHS-month volume-weighted unit value is the headline;
    MoM and YoY compare the same window shifted by 1 and 12 months. Falls back to annual data.
    """
    today = today or date.today()
    rows = session.exec(select(TradeValuation)).all()
    by_c: dict[str, list[TradeValuation]] = defaultdict(list)
    for r in rows:
        by_c[r.country_iso].append(r)
    out: dict[str, dict[str, Any]] = {}
    for iso, rs in by_c.items():
        monthly = [r for r in rs if r.freq == "M"]
        recent_cut = today - timedelta(days=MONTHLY_MAX_AGE_DAYS)
        if monthly and max(r.period_month for r in monthly) >= recent_cut:
            latest = max(r.period_month for r in monthly)
            year_cut = _shift_month(latest, 11)
            vol: dict[str, float] = defaultdict(float)
            for r in monthly:
                if r.period_month >= year_cut:
                    vol[r.reporter] += r.import_volume_kg
            reporter = max(vol, key=vol.get)
            series = [r for r in monthly if r.reporter == reporter]
            end = max(r.period_month for r in series)

            def window(shift: int) -> tuple[float | None, float]:
                hi = _shift_month(end, shift)
                lo = _shift_month(hi, TRADE_WINDOW_MONTHS - 1)
                return _wavg([r for r in series if lo <= r.period_month <= hi])

            val, kg = window(0)
            prev, _ = window(1)
            yoy, _ = window(12)
            out[iso] = {
                "unit_usd_lb": val,
                "period": end.isoformat(),
                "window_start": _shift_month(end, TRADE_WINDOW_MONTHS - 1).isoformat(),
                "freq": "M",
                "reporters": [reporter],
                "volume_kg": kg,
                "chg_mom_pct": pct_change(val, prev),
                "chg_yoy_pct": pct_change(val, yoy),
            }
            continue
        annual = [r for r in rs if r.freq == "A"]
        if not annual:
            continue
        year = max(r.period_month for r in annual)
        cur = [r for r in annual if r.period_month == year]
        # Same reporters the year before, so YoY isn't skewed by a market appearing/disappearing.
        reps = {r.reporter for r in cur}
        prev_year = [r for r in annual if r.period_month == date(year.year - 1, 1, 1) and r.reporter in reps]
        val, kg = _wavg(cur)
        prev, _ = _wavg(prev_year)
        out[iso] = {
            "unit_usd_lb": val,
            "period": year.isoformat(),
            "window_start": year.isoformat(),
            "freq": "A",
            "reporters": sorted(reps),
            "volume_kg": kg,
            "chg_mom_pct": None,
            "chg_yoy_pct": pct_change(val, prev),
        }
    return out


def trade_series(session: Session, country: str) -> list[dict[str, Any]]:
    rows = session.exec(
        select(TradeValuation).where(TradeValuation.country_iso == country).order_by(TradeValuation.period_month)
    ).all()
    return [
        {"period": r.period_month.isoformat(), "freq": r.freq, "reporter": r.reporter,
         "unit_usd_lb": r.unit_price_usd_lb, "volume_kg": r.import_volume_kg, "source": r.source}
        for r in rows
    ]


# ---------------------------------------------------------------- matrix

def country_matrix(session: Session, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    now = _eod(today)
    countries = session.exec(select(Country).order_by(Country.region, Country.name)).all()
    ice = ice_series(session, today - timedelta(days=800))
    c_now = asof(ice, today)
    idx = spot_index(session, today - timedelta(days=400))
    spot_now = idx.avg(now)
    spot_7 = idx.avg(now - timedelta(days=7))
    spot_30 = idx.avg(now - timedelta(days=30))
    spot_1y = idx.avg(now - timedelta(days=365))
    trade = latest_trade(session, today)

    se_counts: dict[str, int] = defaultdict(int)
    for o in current_offers(session):
        if o.is_southeast and o.country_iso:
            se_counts[o.country_iso] += 1

    rows = []
    for c in countries:
        s = spot_now.get(c.iso_code)
        t = trade.get(c.iso_code)
        spot_avg = round(s[0], 4) if s else None
        if spot_avg is not None:
            diff, diff_basis = (round(spot_avg - c_now, 4) if c_now else None), "spot"
            def then(snap: dict) -> float | None:
                prev = snap.get(c.iso_code)
                return prev[0] if prev and prev[2] == s[2] else None  # compare same pack basis only

            chg_7 = pct_change(spot_avg, then(spot_7))
            chg_30 = pct_change(spot_avg, then(spot_30))
            chg_yoy = pct_change(spot_avg, then(spot_1y))
            change_basis = "spot"
        else:
            diff = diff_basis = chg_7 = None
            chg_30 = chg_yoy = None
            change_basis = None
        if t:
            if chg_30 is None and chg_yoy is None:
                chg_30, chg_yoy, change_basis = t["chg_mom_pct"], t["chg_yoy_pct"], "trade"
            if diff is None and c_now and t["unit_usd_lb"]:
                # Customs unit value vs the C price averaged over the same window.
                pstart = date.fromisoformat(t["window_start"])
                if t["freq"] == "A":
                    pend = date(pstart.year + 1, 1, 1)
                else:
                    pend = _shift_month(date.fromisoformat(t["period"]), -1)
                window = [v for d, v in ice if pstart <= d < pend]
                ref = mean(window) if window else None
                if ref:
                    diff, diff_basis = round(t["unit_usd_lb"] - ref, 4), "trade"
        rows.append({
            "iso_code": c.iso_code,
            "m49": c.m49,
            "name": c.name,
            "region": c.region,
            "species_mix": c.species_mix,
            "currency_code": c.currency_code,
            "ico_group": c.ico_group,
            "spot_avg_usd_lb": spot_avg,
            "spot_offer_count": s[1] if s else 0,
            "spot_basis": s[2] if s else None,
            "southeast_offer_count": se_counts.get(c.iso_code, 0),
            "diff_vs_c_usd_lb": diff,
            "diff_basis": diff_basis,
            "typical_diff_usd_lb": [c.typical_diff_low, c.typical_diff_high],
            "trade_unit_usd_lb": t["unit_usd_lb"] if t else None,
            "trade_period": t["period"] if t else None,
            "trade_freq": t["freq"] if t else None,
            "trade_reporters": t["reporters"] if t else [],
            "chg_7d_pct": chg_7,
            "chg_30d_pct": chg_30,
            "chg_yoy_pct": chg_yoy,
            "change_basis": change_basis,
            "harvest_status": harvest_status(c.main_harvest_months, c.fly_harvest_months, today),
            "main_harvest_months": c.main_harvest_months,
            "fly_harvest_months": c.fly_harvest_months,
        })
    return {"as_of": today.isoformat(), "ice_arabica_usd_lb": c_now, "countries": rows}


# ---------------------------------------------------------------- history

RANGES = {"30d": 30, "90d": 90, "1y": 365, "3y": 365 * 3}


def history(
    session: Session, country: str, process: str | None = None, range_: str = "1y", today: date | None = None
) -> dict[str, Any]:
    today = today or date.today()
    days = RANGES.get(range_, 365)
    start = today - timedelta(days=days)
    country = country.upper()
    ice = ice_series(session, start - timedelta(days=10))
    idx = spot_index(session, start, country=country, process=process)
    points = []
    step = 1 if days <= 120 else (3 if days <= 400 else 7)
    # Step backwards from today so the latest point is always today (fresh scrapes show immediately).
    days_list = [today - timedelta(days=i) for i in range(0, days + 1, step)][::-1]
    for d in days_list:
        c = asof(ice, d)
        s = idx.avg(_eod(d), country=country, process=process).get(country)
        if c is not None or s is not None:
            points.append({
                "date": d.isoformat(),
                "ice_c_usd_lb": c,
                "spot_avg_usd_lb": round(s[0], 4) if s else None,
                "spot_n": s[1] if s else 0,
                "spot_basis": s[2] if s else None,
                "diff_usd_lb": round(s[0] - c, 4) if s and c is not None else None,
            })
    trade = [
        t for t in trade_series(session, country)
        if date.fromisoformat(t["period"]) >= start - timedelta(days=31)
    ]
    # Differential trend: slope of the spread over the window (USD/lb per 30 days).
    diffs = [(i, p["diff_usd_lb"]) for i, p in enumerate(points) if p["diff_usd_lb"] is not None]
    trend = None
    if len(diffs) >= 3:
        xs, ys = zip(*diffs)
        mx, my = mean(xs), mean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den:
            slope = sum((x - mx) * (y - my) for x, y in diffs) / den
            per30 = slope * (30 / step)
            trend = {"usd_lb_per_30d": round(per30, 4),
                     "direction": "widening" if per30 > 0.005 else ("compressing" if per30 < -0.005 else "flat")}
    processes = sorted(
        session.exec(
            select(SpotOffer.process)
            .where(SpotOffer.country_iso == country, SpotOffer.process.is_not(None))
            .distinct()
        ).all()
    )
    return {"country": country, "process": process, "range": range_, "points": points,
            "trade": trade, "spread_trend": trend, "processes": processes}
