"""Engine, sessions, schema creation and country seeding."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from . import config
from .models import Country, IngestRun, utcnow

_engine = None


def get_engine(url: str | None = None):
    global _engine
    if url is not None:
        return _make_engine(url)
    if _engine is None:
        _engine = _make_engine(config.DATABASE_URL)
    return _engine


def _make_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":///" in url and ":memory:" not in url:
            Path(url.split(":///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def set_engine(engine) -> None:
    """Swap the global engine (used by tests)."""
    global _engine
    _engine = engine


def init_db(engine=None) -> None:
    engine = engine or get_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_countries(session)


@contextmanager
def session_scope(engine=None) -> Iterator[Session]:
    with Session(engine or get_engine()) as session:
        yield session


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine()) as session:
        yield session


def load_seed(path: Path | None = None) -> list[dict]:
    path = path or config.SEED_FILE
    with path.open(encoding="utf-8") as f:
        return json.load(f)["countries"]


def seed_countries(session: Session, path: Path | None = None) -> int:
    """Insert or update countries from the seed file. Returns number of rows written."""
    rows = load_seed(path)
    existing = {c.iso_code: c for c in session.exec(select(Country)).all()}
    n = 0
    for r in rows:
        lo, hi = (r.get("typical_diff_usd_lb") or [None, None])[:2]
        values = dict(
            iso_code=r["iso_code"],
            m49=int(r["m49"]),
            name=r["name"],
            region=r["region"],
            species_mix=r["species_mix"],
            currency_code=r["currency_code"],
            ico_group=r["ico_group"],
            main_harvest_months=list(r.get("main_harvest_months", [])),
            fly_harvest_months=list(r.get("fly_harvest_months", [])),
            default_bag_kg=float(r.get("default_bag_kg", 60)),
            typical_diff_low=lo,
            typical_diff_high=hi,
        )
        if r["iso_code"] in existing:
            c = existing[r["iso_code"]]
            changed = False
            for k, v in values.items():
                if getattr(c, k) != v:
                    setattr(c, k, v)
                    changed = True
            if changed:
                session.add(c)
                n += 1
        else:
            session.add(Country(**values))
            n += 1
    session.commit()
    return n


class RunLogger:
    """Records an IngestRun row for an ETL job."""

    def __init__(self, session: Session, job: str):
        self.session = session
        self.run = IngestRun(job=job)
        session.add(self.run)
        session.commit()
        session.refresh(self.run)

    def finish(self, status: str, rows: int = 0, message: str = "") -> IngestRun:
        self.run.status = status
        self.run.rows = rows
        self.run.message = message[:4000]
        self.run.finished_at = utcnow()
        self.session.add(self.run)
        self.session.commit()
        return self.run


def last_successful_run(session: Session, job: str) -> datetime | None:
    run = session.exec(
        select(IngestRun)
        .where(IngestRun.job == job, IngestRun.status.in_(["ok", "partial"]))
        .order_by(IngestRun.started_at.desc())
    ).first()
    return run.finished_at if run else None
