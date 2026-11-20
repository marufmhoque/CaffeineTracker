"""ETL job entry points shared by the scheduler, the API and the CLI."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from .analytics import benchmark_summary
from .alerts import build_digest, deliver_digest, run_alert_engine
from .db import session_scope
from .pipelines import benchmarks, spot, trade

log = logging.getLogger(__name__)

_locks: dict[str, threading.Lock] = {}


def _locked(name: str, fn: Callable[[], Any]) -> Any:
    lock = _locks.setdefault(name, threading.Lock())
    if not lock.acquire(blocking=False):
        return {"status": "skipped", "message": f"{name} already running"}
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.exception("job %s failed", name)
        return {"status": "error", "message": str(e)}
    finally:
        lock.release()


def job_benchmarks() -> Any:
    def run():
        with session_scope() as s:
            return benchmarks.run(s)
    return _locked("benchmarks", run)


def job_trade() -> Any:
    def run():
        with session_scope() as s:
            return trade.run(s)
    return _locked("trade", run)


def job_scrapers(only: list[str] | None = None) -> Any:
    def run():
        with session_scope() as s:
            return spot.run_scrapers(s, only=only)
    return _locked("scrapers", run)


def job_portals() -> Any:
    def run():
        from scrapers.portal import run_portals

        res = run_portals()
        with session_scope() as s:
            res["dropzone"] = spot.process_dropzone(s)
        return res
    return _locked("portals", run)


def job_dropzone() -> Any:
    def run():
        with session_scope() as s:
            return spot.process_dropzone(s)
    return _locked("dropzone", run)


def job_alerts(deliver: bool = True) -> Any:
    def run():
        with session_scope() as s:
            created = run_alert_engine(s)
            digest = build_digest(created, benchmark_summary(s))
            delivery = deliver_digest(digest) if deliver and created else {}
            return {"status": "ok", "created": len(created), "digest": digest, "delivery": delivery}
    return _locked("alerts", run)


JOBS: dict[str, Callable[[], Any]] = {
    "benchmarks": job_benchmarks,
    "trade": job_trade,
    "scrapers": job_scrapers,
    "portals": job_portals,
    "dropzone": job_dropzone,
    "alerts": job_alerts,
}


def run_all() -> dict[str, Any]:
    return {name: JOBS[name]() for name in ("benchmarks", "trade", "dropzone", "scrapers", "alerts")}
