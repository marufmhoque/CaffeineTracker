"""Origin weather anomaly tracker (Open-Meteo, no key).

For each growing belt in data/growing_belts.json:
- recent: last 90 days of rain, max/min temperature plus a 10-day forecast (forecast API, past_days=92)
- normal: the same 90-day window in each of the previous 10 years (archive API, ERA5)

Risk score (heuristic, documented so it can be tuned):
  rain < 50% of normal  +2   |  rain < 75% of normal  +1      (skipped in a dry season: normal < 60 mm)
  >= 15 hot days in 30d +2   |  >= 7 hot days in 30d   +1      (Tmax >= 32°C Arabica, >= 35°C Robusta)
  >= 4 hot days in the 10-day forecast                 +1
  frost (Tmin <= 1°C) in the last 30 days              +2      (Arabica belts)
  frost in the 10-day forecast                         +2
  0 -> Low, 1-2 -> Moderate, >= 3 -> Critical
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlmodel import Session, select

from .. import config
from ..db import RunLogger
from ..http import get_with_retry, make_client
from ..models import WeatherSignal

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY = "precipitation_sum,temperature_2m_max,temperature_2m_min"
WINDOW_DAYS = 90
NORMAL_YEARS = 10
DRY_SEASON_MM = 60.0


def belts_file() -> Path:
    return config.DATA_DIR / "growing_belts.json"


def load_belts(path: Path | None = None) -> list[dict[str, Any]]:
    with (path or belts_file()).open(encoding="utf-8") as f:
        return json.load(f)["belts"]


def _coords(belts: list[dict[str, Any]]) -> dict[str, str]:
    return {"latitude": ",".join(str(b["lat"]) for b in belts),
            "longitude": ",".join(str(b["lon"]) for b in belts)}


def _as_list(payload: Any) -> list[dict[str, Any]]:
    return payload if isinstance(payload, list) else [payload]


def _daily(loc: dict[str, Any]) -> dict[date, tuple[float | None, float | None, float | None]]:
    d = loc.get("daily") or {}
    out = {}
    for t, p, hi, lo in zip(d.get("time", []), d.get("precipitation_sum", []),
                            d.get("temperature_2m_max", []), d.get("temperature_2m_min", [])):
        out[date.fromisoformat(t)] = (p, hi, lo)
    return out


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year - years, day=28)


def score_belt(
    belt: dict[str, Any],
    recent: dict[date, tuple[float | None, float | None, float | None]],
    history: dict[date, tuple[float | None, float | None, float | None]],
    today: date,
) -> dict[str, Any]:
    arabica = belt["species"] == "Arabica"
    hot_t = 32.0 if arabica else 35.0
    start = today - timedelta(days=WINDOW_DAYS)
    past = {d: v for d, v in recent.items() if start <= d < today}
    future = {d: v for d, v in recent.items() if d >= today}
    rain = sum(v[0] or 0 for v in past.values()) if past else None

    normals = []
    for k in range(1, NORMAL_YEARS + 1):
        lo, hi = _shift_years(start, k), _shift_years(today, k)
        vals = [v[0] for d, v in history.items() if lo <= d < hi and v[0] is not None]
        if len(vals) >= WINDOW_DAYS * 0.9:
            normals.append(sum(vals))
    normal = sum(normals) / len(normals) if normals else None
    pct = round(rain / normal * 100, 1) if rain is not None and normal else None

    last30 = [v for d, v in past.items() if d >= today - timedelta(days=30)]
    hot30 = sum(1 for v in last30 if v[1] is not None and v[1] >= hot_t)
    frost30 = sum(1 for v in last30 if v[2] is not None and v[2] <= 1.0) if arabica else 0
    hot_fc = sum(1 for v in future.values() if v[1] is not None and v[1] >= hot_t)
    frost_fc = sum(1 for v in future.values() if v[2] is not None and v[2] <= 1.0) if arabica else 0

    score, reasons = 0, []
    if pct is not None and normal is not None and normal >= DRY_SEASON_MM:
        if pct < 50:
            score += 2
            reasons.append(f"Rainfall {pct:.0f}% of normal (90 days)")
        elif pct < 75:
            score += 1
            reasons.append(f"Rainfall {pct:.0f}% of normal (90 days)")
    if hot30 >= 15:
        score += 2
        reasons.append(f"{hot30} days ≥{hot_t:.0f}°C in the last 30")
    elif hot30 >= 7:
        score += 1
        reasons.append(f"{hot30} days ≥{hot_t:.0f}°C in the last 30")
    if hot_fc >= 4:
        score += 1
        reasons.append(f"{hot_fc} hot days forecast (10 days)")
    if frost30:
        score += 2
        reasons.append(f"Frost on {frost30} day(s) in the last 30")
    if frost_fc:
        score += 2
        reasons.append(f"Frost forecast on {frost_fc} day(s)")
    risk = "Low" if score == 0 else ("Moderate" if score <= 2 else "Critical")
    return {
        "rain_90d_mm": round(rain, 1) if rain is not None else None,
        "rain_normal_mm": round(normal, 1) if normal is not None else None,
        "rain_pct_normal": pct,
        "hot_days_30d": hot30,
        "frost_days_30d": frost30,
        "hot_forecast_days": hot_fc,
        "frost_forecast_days": frost_fc,
        "risk": risk,
        "reasons": reasons or ["Rain and temperatures within normal range"],
    }


def run(session: Session, client: httpx.Client | None = None, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    logger = RunLogger(session, "weather")
    belts = load_belts()
    own = client is None
    client = client or make_client()
    errors: list[str] = []
    n = 0
    try:
        recent = _as_list(get_with_retry(client, FORECAST_URL, params={
            **_coords(belts), "daily": DAILY, "past_days": WINDOW_DAYS + 2, "forecast_days": 10,
            "timezone": "auto"}).json())
        start = _shift_years(today - timedelta(days=WINDOW_DAYS + 5), NORMAL_YEARS)
        history = _as_list(get_with_retry(client, ARCHIVE_URL, params={
            **_coords(belts), "daily": DAILY, "start_date": start.isoformat(),
            "end_date": _shift_years(today, 1).isoformat(), "timezone": "auto"}).json())
        if len(recent) != len(belts) or len(history) != len(belts):
            raise ValueError("Open-Meteo returned a different number of locations than requested")
        for belt, r, h in zip(belts, recent, history):
            s = score_belt(belt, _daily(r), _daily(h), today)
            row = session.exec(
                select(WeatherSignal).where(WeatherSignal.as_of == today, WeatherSignal.belt == belt["belt"])
            ).first() or WeatherSignal(as_of=today, belt=belt["belt"], country_iso=belt["country_iso"],
                                       species=belt["species"], lat=belt["lat"], lon=belt["lon"])
            for k, v in s.items():
                if k == "reasons":
                    row.reasons_json = v
                else:
                    setattr(row, k, v)
            session.add(row)
            n += 1
        session.commit()
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))
        log.warning("weather run failed: %s", e)
    finally:
        if own:
            client.close()
    status = "error" if n == 0 else ("partial" if errors else "ok")
    logger.finish(status, n, "; ".join(errors))
    return {"status": status, "rows": n, "errors": errors}
