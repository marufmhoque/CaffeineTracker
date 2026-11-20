"""APScheduler background schedule (America/New_York)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import select

from . import config
from .db import session_scope
from .jobs import (
    job_alerts,
    job_benchmarks,
    job_dropzone,
    job_portals,
    job_scrapers,
    job_trade,
)
from .models import MarketBenchmark, TradeValuation

log = logging.getLogger(__name__)


def build_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=config.TIMEZONE, job_defaults={"coalesce": True, "max_instances": 1,
                                                                         "misfire_grace_time": 3600})
    # ICE settles ~1:30pm ET; Yahoo has the settlement by early evening.
    sched.add_job(job_benchmarks, "cron", day_of_week="mon-fri", hour=18, minute=15, id="benchmarks")
    # Comtrade/Census monthly data lands irregularly; weekly refresh is plenty.
    sched.add_job(job_trade, "cron", day_of_week="sun", hour=5, minute=0, id="trade")
    sched.add_job(job_portals, "cron", day_of_week="mon-fri", hour=6, minute=30, id="portals")
    sched.add_job(job_scrapers, "cron", hour=7, minute=0, id="scrapers")
    sched.add_job(job_dropzone, "interval", minutes=5, id="dropzone")
    sched.add_job(job_alerts, "cron", hour=7, minute=45, id="alerts")

    if config.BOOTSTRAP_ON_START:
        with session_scope() as s:
            no_bench = s.exec(select(MarketBenchmark.id).limit(1)).first() is None
            no_trade = s.exec(select(TradeValuation.id).limit(1)).first() is None
        soon = datetime.now() + timedelta(seconds=5)
        if no_bench:
            sched.add_job(job_benchmarks, "date", run_date=soon, id="bootstrap_benchmarks")
        if no_trade:
            sched.add_job(job_trade, "date", run_date=soon + timedelta(seconds=20), id="bootstrap_trade")
    return sched
