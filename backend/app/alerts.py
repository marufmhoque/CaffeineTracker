"""Buying-opportunity alert engine + daily digest delivery (terminal, Slack webhook, SMTP)."""

from __future__ import annotations

import logging
import smtplib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from statistics import mean, median
from typing import Any

import httpx
from sqlalchemy import func
from sqlmodel import Session, select

from . import config
from .analytics import _eod, current_offers, fx_series, is_bulk, pct_change, spot_index
from .models import Alert, Country, SpotOffer, utcnow

log = logging.getLogger(__name__)

ORIGIN_DROP = "origin_price_drop"
VALUE_DEAL = "value_per_point"
SE_ARRIVAL = "southeast_arrival"
FX_TRIGGER = "fx_weakening"
ALERT_TYPES = [ORIGIN_DROP, VALUE_DEAL, SE_ARRIVAL, FX_TRIGGER]

DEDUPE_DAYS = 7


def _exists(session: Session, dedupe_key: str, within_days: int = DEDUPE_DAYS) -> bool:
    since = utcnow() - timedelta(days=within_days)
    return session.exec(
        select(Alert.id).where(Alert.dedupe_key == dedupe_key, Alert.created_at >= since)
    ).first() is not None


def _add(session: Session, created: list[Alert], **kw) -> None:
    if _exists(session, kw["dedupe_key"]):
        return
    a = Alert(**kw)
    session.add(a)
    created.append(a)


def detect_origin_drops(session: Session, today: date, names: dict[str, str]) -> list[dict]:
    days = config.ALERT_ORIGIN_DROP_DAYS
    idx = spot_index(session, today - timedelta(days=days + 1))
    now = idx.avg(_eod(today))
    then = idx.avg(_eod(today - timedelta(days=days)))
    out = []
    for iso, (avg_now, n_now, basis) in now.items():
        if iso not in then or then[iso][2] != basis:
            continue
        avg_then = then[iso][0]
        chg = pct_change(avg_now, avg_then)
        if chg is not None and chg <= -config.ALERT_ORIGIN_DROP_PCT:
            out.append({
                "country_iso": iso,
                "message": f"{names.get(iso, iso)} spot average down {abs(chg):.1f}% over {days} days "
                           f"(${avg_then:.2f} → ${avg_now:.2f}/lb, {n_now} offers)",
                "dedupe_key": f"{ORIGIN_DROP}:{iso}:{today.isocalendar()[1]}",
                "data_json": {"pct": chg, "avg_now": avg_now, "avg_then": avg_then},
            })
    return out


def detect_value_deals(session: Session, today: date, names: dict[str, str]) -> list[dict]:
    """Lots scoring >= ALERT_MIN_SCORE priced below the 30-day average for their origin + process."""
    start = datetime.now(timezone.utc) - timedelta(days=30)
    rows = session.exec(
        select(SpotOffer.country_iso, SpotOffer.process, SpotOffer.bag_weight_kg, SpotOffer.spot_price_usd_lb)
        .where(SpotOffer.scraped_at >= start, SpotOffer.country_iso.is_not(None))
    ).all()
    # Compare within the same pack class: boxes carry a small-lot premium over full bags.
    window: dict[tuple, list[float]] = defaultdict(list)
    for iso, proc, bag_kg, price in rows:
        window[(iso, proc, is_bulk(bag_kg))].append(price)
    out = []
    for o in current_offers(session, min_score=config.ALERT_MIN_SCORE):
        prices = window.get((o.country_iso, o.process, is_bulk(o.bag_weight_kg)), [])
        if len(prices) < 3:
            continue
        ma = mean(prices)
        if o.spot_price_usd_lb < ma:
            disc = (ma - o.spot_price_usd_lb) / ma * 100
            out.append({
                "country_iso": o.country_iso,
                "message": f"{o.sca_score:g}-pt {names.get(o.country_iso, o.country_iso)} {o.process or ''} "
                           f"“{o.lot_name}” from {o.importer} at ${o.spot_price_usd_lb:.2f}/lb — "
                           f"{disc:.1f}% under the 30-day avg (${ma:.2f}); landed ATL ${o.atlanta_landed_usd_lb:.2f}/lb"
                           f"{' · ' + o.warehouse_location if o.warehouse_location else ''}",
                "dedupe_key": f"{VALUE_DEAL}:{o.offer_key}:{round(o.spot_price_usd_lb, 2)}",
                "data_json": {"offer_id": o.id, "price": o.spot_price_usd_lb, "ma30": ma, "score": o.sca_score},
            })
    return out


def detect_southeast_arrivals(session: Session, today: date, names: dict[str, str]) -> list[dict]:
    """Offers newly appearing in a Southeast warehouse (Savannah / Charleston / Jacksonville).

    Only flags importers we've seen before, so the very first scrape doesn't announce everything.
    """
    out = []
    current = [o for o in current_offers(session) if o.is_southeast]
    if not current:
        return out
    keys = {o.offer_key for o in current}
    first_seen = dict(
        session.exec(
            select(SpotOffer.offer_key, func.min(SpotOffer.scraped_at))
            .where(SpotOffer.offer_key.in_(keys))
            .group_by(SpotOffer.offer_key)
        ).all()
    )
    importer_first = dict(
        session.exec(select(SpotOffer.importer, func.min(SpotOffer.scraped_at)).group_by(SpotOffer.importer)).all()
    )
    for o in current:
        fs = first_seen.get(o.offer_key)
        imp_fs = importer_first.get(o.importer)
        if not fs or not imp_fs:
            continue
        if fs <= imp_fs:  # present in the importer's first-ever batch -> not an "arrival"
            continue
        if fs.replace(tzinfo=fs.tzinfo or timezone.utc) < datetime.now(timezone.utc) - timedelta(days=3):
            continue
        out.append({
            "country_iso": o.country_iso,
            "message": f"New in {o.warehouse_location}: {names.get(o.country_iso, o.country_iso or '?')} "
                       f"“{o.lot_name}” ({o.importer}) ${o.spot_price_usd_lb:.2f}/lb → "
                       f"${o.atlanta_landed_usd_lb:.2f}/lb landed ATL"
                       f"{f', {o.bags_available} bags' if o.bags_available else ''}",
            "dedupe_key": f"{SE_ARRIVAL}:{o.offer_key}",
            "data_json": {"offer_id": o.id},
        })
    return out


def detect_fx_triggers(session: Session, today: date) -> list[dict]:
    series = fx_series(session, today - timedelta(days=30))
    countries = session.exec(select(Country)).all()
    by_ccy: dict[str, list[str]] = defaultdict(list)
    for c in countries:
        by_ccy[c.currency_code].append(c.iso_code)
    out = []
    for ccy, s in series.items():
        if len(s) < 6:
            continue
        d_now = s[-1][0]
        # Median of the last 3 observations vs. the 3 ending a week earlier: one bad print can't trigger.
        now = median(v for _, v in s[-3:])
        before = [v for d, v in s if d <= d_now - timedelta(days=7)][-3:]
        if not before:
            continue
        then = median(before)
        chg = pct_change(now, then)
        if chg is None or chg < config.ALERT_FX_WEAKEN_PCT:
            continue  # USD/XXX rising = local currency weakening
        isos = by_ccy.get(ccy, [])
        out.append({
            "country_iso": isos[0] if len(isos) == 1 else None,
            "message": f"{ccy} weakened {chg:.1f}% vs USD in a week ({then:,.2f} → {now:,.2f} per USD) — "
                       f"export prices from {', '.join(isos) or ccy} may soften",
            "dedupe_key": f"{FX_TRIGGER}:{ccy}:{d_now.isocalendar()[1]}",
            "data_json": {"currency": ccy, "pct": chg, "countries": isos},
        })
    return out


def run_alert_engine(session: Session, today: date | None = None) -> list[Alert]:
    today = today or date.today()
    names = {c.iso_code: c.name for c in session.exec(select(Country)).all()}
    created: list[Alert] = []
    for alert_type, found in (
        (ORIGIN_DROP, detect_origin_drops(session, today, names)),
        (VALUE_DEAL, detect_value_deals(session, today, names)),
        (SE_ARRIVAL, detect_southeast_arrivals(session, today, names)),
        (FX_TRIGGER, detect_fx_triggers(session, today)),
    ):
        for f in found:
            _add(session, created, alert_type=alert_type, **f)
    session.commit()
    for a in created:
        session.refresh(a)
    return created


# ---------------------------------------------------------------- digest

TITLES = {
    ORIGIN_DROP: "Origin price drops",
    VALUE_DEAL: "High value-per-point deals",
    SE_ARRIVAL: "Southeast warehouse arrivals",
    FX_TRIGGER: "FX triggers",
}


def build_digest(alerts: list[Alert], summary: dict[str, Any] | None = None) -> str:
    lines = [f"OriginPulse daily digest — {date.today():%a %d %b %Y}"]
    if summary and summary.get("ice_arabica", {}).get("usd_lb"):
        ice = summary["ice_arabica"]
        lines.append(
            f"ICE 'C' ${ice['usd_lb']:.4f}/lb (30d MA ${ice['ma30_usd_lb'] or 0:.4f}, "
            f"52w ${ice['low_52w'] or 0:.2f}–${ice['high_52w'] or 0:.2f})"
        )
    if not alerts:
        lines.append("No new alerts.")
        return "\n".join(lines)
    grouped: dict[str, list[Alert]] = defaultdict(list)
    for a in alerts:
        grouped[a.alert_type].append(a)
    for t in ALERT_TYPES:
        if grouped.get(t):
            lines.append("")
            lines.append(f"{TITLES[t]} ({len(grouped[t])})")
            lines.extend(f"  • {a.message}" for a in grouped[t])
    return "\n".join(lines)


def deliver_digest(text: str) -> dict[str, str]:
    results: dict[str, str] = {}
    if config.SLACK_WEBHOOK_URL:
        try:
            r = httpx.post(config.SLACK_WEBHOOK_URL, json={"text": f"```{text}```"}, timeout=15)
            r.raise_for_status()
            results["slack"] = "sent"
        except Exception as e:  # noqa: BLE001
            results["slack"] = f"error: {e}"
    if config.SMTP_HOST and config.SMTP_TO:
        try:
            msg = EmailMessage()
            msg["Subject"] = text.splitlines()[0]
            msg["From"] = config.SMTP_FROM or config.SMTP_USER
            msg["To"] = config.SMTP_TO
            msg.set_content(text)
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
                s.starttls()
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
            results["email"] = "sent"
        except Exception as e:  # noqa: BLE001
            results["email"] = f"error: {e}"
    return results
