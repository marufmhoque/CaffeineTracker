"""OriginPulse API (FastAPI). Run: uvicorn backend.app.main:app --reload"""

from __future__ import annotations

import hmac
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from scrapers import SCRAPERS
from scrapers.portal import load_portals

from . import analytics, config, costing
from .alerts import ALERT_TYPES
from .db import get_session, init_db
from .harvest import harvest_status
from .jobs import JOBS, run_all
from .models import Alert, Country, IngestedFile, IngestRun, SpotOffer
from .pipelines import spot
from .units import LB_PER_KG, convert_price

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("originpulse")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    costing.load_rules()  # writes defaults on first run
    config.SPOT_SHEETS_DIR.mkdir(parents=True, exist_ok=True)
    sched = None
    if config.ENABLE_SCHEDULER:
        from .scheduler import build_scheduler

        sched = build_scheduler()
        sched.start()
        log.info("scheduler started: %s", [j.id for j in sched.get_jobs()])
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="OriginPulse", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"]
)


# ---------------------------------------------------------------- helpers

def offer_out(o: SpotOffer, shrink: float, names: dict[str, str]) -> dict[str, Any]:
    landed = o.atlanta_landed_usd_lb
    return {
        "id": o.id,
        "importer": o.importer,
        "source": o.source,
        "scraped_at": o.scraped_at.isoformat(),
        "offer_date": o.offer_date.isoformat() if o.offer_date else None,
        "country_iso": o.country_iso,
        "country": names.get(o.country_iso or "", None),
        "lot_name": o.lot_name,
        "origin_region": o.origin_region,
        "process": o.process,
        "variety": o.variety,
        "sca_score": o.sca_score,
        "bag_weight_kg": o.bag_weight_kg,
        "warehouse_location": o.warehouse_location,
        "warehouse_tier": o.warehouse_tier,
        "is_southeast": o.is_southeast,
        "spot_usd_lb": o.spot_price_usd_lb,
        "freight_usd_lb": o.freight_usd_lb,
        "landed_usd_lb": landed,
        "roasted_usd_lb": round(costing.roasted_cost(landed, shrink), 4) if landed else None,
        "bags_available": o.bags_available,
        "raw_url": o.raw_url if (o.raw_url or "").startswith("http") else None,
    }


def _names(session: Session) -> dict[str, str]:
    return {c.iso_code: c.name for c in session.exec(select(Country)).all()}


# ---------------------------------------------------------------- read endpoints

@app.get("/api/health")
def health(session: Session = Depends(get_session)):
    runs = session.exec(select(IngestRun).order_by(IngestRun.started_at.desc()).limit(1)).first()
    return {"status": "ok", "last_run": runs.started_at.isoformat() if runs else None,
            "scheduler": config.ENABLE_SCHEDULER}


@app.get("/api/countries")
def countries(session: Session = Depends(get_session)):
    out = []
    for c in session.exec(select(Country).order_by(Country.region, Country.name)).all():
        d = c.model_dump()
        d["harvest_status"] = harvest_status(c.main_harvest_months, c.fly_harvest_months)
        out.append(d)
    return out


@app.get("/api/matrix")
def matrix(session: Session = Depends(get_session)):
    return analytics.country_matrix(session)


@app.get("/api/benchmarks/summary")
def benchmarks_summary(session: Session = Depends(get_session)):
    return analytics.benchmark_summary(session)


@app.get("/api/benchmarks/history")
def benchmarks_history(days: int = Query(365, ge=7, le=3650), session: Session = Depends(get_session)):
    from datetime import date, timedelta

    from .models import MarketBenchmark

    rows = session.exec(
        select(MarketBenchmark)
        .where(MarketBenchmark.date >= date.today() - timedelta(days=days))
        .order_by(MarketBenchmark.date)
    ).all()
    return [
        {"date": r.date.isoformat(), "ice_arabica_usd_lb": r.ice_arabica_usd_lb,
         "ice_robusta_usd_lb": r.ice_robusta_usd_lb, "ico_composite_usd_lb": r.ico_composite_usd_lb,
         "fx": r.fx_rates_json}
        for r in rows
    ]


@app.get("/api/offers")
def offers(
    country: Optional[str] = None,
    process: Optional[str] = None,
    min_score: Optional[float] = None,
    southeast: bool = False,
    importer: Optional[str] = None,
    shrinkage: Optional[float] = Query(None, ge=0, lt=0.5),
    session: Session = Depends(get_session),
):
    rules = costing.load_rules()
    shrink = rules["default_shrinkage"] if shrinkage is None else shrinkage
    names = _names(session)
    rows = analytics.current_offers(
        session, country=country, process=process, min_score=min_score, southeast_only=southeast, importer=importer
    )
    return {"shrinkage": shrink, "offers": [offer_out(o, shrink, names) for o in rows]}


@app.get("/api/offers/facets")
def offer_facets(session: Session = Depends(get_session)):
    rows = analytics.current_offers(session)
    return {
        "importers": sorted({o.importer for o in rows}),
        "processes": sorted({o.process for o in rows if o.process}),
        "countries": sorted({o.country_iso for o in rows if o.country_iso}),
        "count": len(rows),
    }


@app.get("/api/history")
def history(
    country: str,
    process: Optional[str] = None,
    range: str = Query("1y", pattern="^(30d|90d|1y|3y)$"),
    session: Session = Depends(get_session),
):
    if not session.get(Country, country.upper()):
        raise HTTPException(404, "unknown country")
    return analytics.history(session, country, process, range)


@app.get("/api/trade/{country}")
def trade(country: str, session: Session = Depends(get_session)):
    return analytics.trade_series(session, country.upper())


@app.get("/api/alerts")
def alerts(
    unread: bool = False,
    alert_type: Optional[str] = None,
    limit: int = Query(100, le=500),
    session: Session = Depends(get_session),
):
    q = select(Alert)
    if unread:
        q = q.where(Alert.is_read.is_(False))
    if alert_type:
        q = q.where(Alert.alert_type == alert_type)
    rows = session.exec(q.order_by(Alert.created_at.desc()).limit(limit)).all()
    unread_n = len(session.exec(select(Alert.id).where(Alert.is_read.is_(False))).all())
    return {"unread": unread_n, "types": ALERT_TYPES, "alerts": [a.model_dump(mode="json") for a in rows]}


@app.post("/api/alerts/{alert_id}/read")
def mark_read(alert_id: int, session: Session = Depends(get_session)):
    a = session.get(Alert, alert_id)
    if not a:
        raise HTTPException(404)
    a.is_read = True
    session.add(a)
    session.commit()
    return {"ok": True}


@app.post("/api/alerts/read-all")
def mark_all_read(session: Session = Depends(get_session)):
    for a in session.exec(select(Alert).where(Alert.is_read.is_(False))).all():
        a.is_read = True
        session.add(a)
    session.commit()
    return {"ok": True}


# ---------------------------------------------------------------- costing

@app.get("/api/settings/freight")
def get_freight():
    return costing.load_rules()


@app.put("/api/settings/freight")
def put_freight(rules: dict[str, Any]):
    try:
        return costing.save_rules(rules)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(422, str(e)) from e


class LandedRequest(BaseModel):
    price: float = Field(gt=0)
    unit: str = Field("lb", pattern="^(lb|kg|bag)$")
    bag_kg: Optional[float] = Field(None, gt=0)
    warehouse: Optional[str] = None
    freight_usd_lb: Optional[float] = Field(None, ge=0)
    fee_usd_lb: Optional[float] = Field(None, ge=0)
    shrinkage: Optional[float] = Field(None, ge=0, lt=0.5)
    bags: Optional[int] = Field(None, gt=0)


@app.post("/api/calc/landed")
def calc_landed(req: LandedRequest):
    if req.unit == "bag":
        if not req.bag_kg:
            raise HTTPException(422, "bag_kg is required for per-bag prices")
        spot_lb = req.price / (req.bag_kg * LB_PER_KG)
    else:
        spot_lb = convert_price(req.price, req.unit, "lb")
    bd, wm = costing.cost_breakdown(
        spot_lb, req.warehouse, freight_override=req.freight_usd_lb, fee_override=req.fee_usd_lb,
        shrinkage=req.shrinkage,
    )
    out = {
        "warehouse": wm.__dict__,
        "per_lb": bd.__dict__,
        "per_kg": {
            (k.replace("_usd_lb", "_usd_kg")): (round(v * LB_PER_KG, 4) if k != "shrinkage" else v)
            for k, v in bd.__dict__.items()
        },
    }
    if req.bags and req.bag_kg:
        lbs = req.bags * req.bag_kg * LB_PER_KG
        out["order"] = {
            "green_lb": round(lbs, 1),
            "roasted_lb": round(lbs * (1 - bd.shrinkage), 1),
            "green_cost_usd": round(lbs * bd.spot_usd_lb, 2),
            "freight_usd": round(lbs * bd.freight_usd_lb, 2),
            "fees_usd": round(lbs * bd.fee_usd_lb, 2),
            "landed_total_usd": round(lbs * bd.landed_usd_lb, 2),
        }
    return out


# ---------------------------------------------------------------- ingestion

@app.get("/api/ingest/runs")
def ingest_runs(limit: int = Query(50, le=500), session: Session = Depends(get_session)):
    runs = session.exec(select(IngestRun).order_by(IngestRun.started_at.desc()).limit(limit)).all()
    files = session.exec(select(IngestedFile).order_by(IngestedFile.ingested_at.desc()).limit(limit)).all()
    return {"runs": [r.model_dump(mode="json") for r in runs], "files": [f.model_dump(mode="json") for f in files]}


@app.get("/api/ingest/sources")
def ingest_sources():
    return {
        "scrapers": [cls().describe() for cls in SCRAPERS.values()],
        "enabled": config.ENABLED_SCRAPERS,
        "portals": [{"importer": p["importer"], "login_url": p["login_url"]} for p in load_portals()],
        "dropzone": str(config.SPOT_SHEETS_DIR),
        "comtrade_key": bool(config.COMTRADE_API_KEY),
        "census_key": bool(__import__("os").getenv("CENSUS_API_KEY")),
        "llm_extraction": bool(config.ANTHROPIC_API_KEY),
        "email_webhook": bool(config.INGEST_WEBHOOK_TOKEN),
    }


@app.post("/api/ingest/run/{job}")
def ingest_run(job: str, background: BackgroundTasks, wait: bool = False):
    if job != "all" and job not in JOBS:
        raise HTTPException(404, f"unknown job; one of {sorted(JOBS)} or 'all'")
    fn = run_all if job == "all" else JOBS[job]
    if wait:
        return {"job": job, "result": fn()}
    threading.Thread(target=fn, daemon=True, name=f"job-{job}").start()
    return {"job": job, "status": "started"}


@app.post("/api/ingest/upload")
async def ingest_upload(file: UploadFile = File(...), session: Session = Depends(get_session)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large")
    try:
        path = spot.save_upload(file.filename or "upload.csv", data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return spot.ingest_file(session, path)


@app.post("/api/ingest/email")
async def ingest_email(request: Request, token: str = Query(""), session: Session = Depends(get_session)):
    """Inbound-email webhook (Mailgun / SendGrid / Postmark-style multipart). Attachments are ingested."""
    if not config.INGEST_WEBHOOK_TOKEN or not hmac.compare_digest(token, config.INGEST_WEBHOOK_TOKEN):
        raise HTTPException(403, "invalid token")
    form = await request.form()
    results = []
    for _, value in form.multi_items():
        if hasattr(value, "filename") and value.filename:
            data = await value.read()
            if len(data) > MAX_UPLOAD_BYTES:
                results.append({"file": value.filename, "status": "error", "message": "too large"})
                continue
            try:
                path = spot.save_upload(value.filename, data)
            except ValueError as e:
                results.append({"file": value.filename, "status": "skipped", "message": str(e)})
                continue
            results.append(spot.ingest_file(session, path))
    return {"attachments": results}
