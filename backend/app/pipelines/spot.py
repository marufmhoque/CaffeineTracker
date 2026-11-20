"""Tier 2 orchestration: run importer scrapers, ingest spot-sheet files, persist offers with landed cost."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from scrapers import SCRAPERS, OfferRecord, enabled_scrapers
from scrapers.spot_sheets import file_mtime_date, iter_sheet_files, parse_spot_sheet

from .. import config
from ..costing import landed_cost, load_rules, match_warehouse
from ..db import RunLogger
from ..models import Country, IngestedFile, SpotOffer, utcnow

log = logging.getLogger(__name__)


def offer_key(rec: OfferRecord, warehouse: str) -> str:
    raw = "|".join(
        [rec.importer.lower(), re.sub(r"\s+", " ", rec.lot_name.lower()).strip(), warehouse.lower(),
         rec.country_iso or ""]
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def persist_offers(
    session: Session,
    records: list[OfferRecord],
    *,
    source: str,
    batch_id: str | None = None,
    rules: dict[str, Any] | None = None,
) -> int:
    rules = rules or load_rules()
    fee = float(rules["warehouse_fee_usd_lb"])
    batch_id = batch_id or uuid.uuid4().hex[:12]
    known = {c.iso_code: c for c in session.exec(select(Country)).all()}
    now = utcnow()
    n = 0
    for r in records:
        if r.country_iso and r.country_iso not in known:
            r.country_iso = None
        wm = match_warehouse(r.warehouse_location, rules)
        bag = r.bag_weight_kg or (known[r.country_iso].default_bag_kg if r.country_iso else None)
        session.add(
            SpotOffer(
                scraped_at=now,
                batch_id=batch_id,
                source=source,
                importer=r.importer,
                offer_date=r.offer_date or date.today(),
                country_iso=r.country_iso,
                lot_name=r.lot_name[:300],
                origin_region=r.origin_region,
                process=r.process,
                variety=r.variety,
                sca_score=r.sca_score,
                bag_weight_kg=bag,
                warehouse_location=wm.warehouse if wm.matched else (r.warehouse_location or None),
                warehouse_tier=wm.tier_id,
                is_southeast=wm.southeast,
                spot_price_usd_lb=r.spot_price_usd_lb,
                freight_usd_lb=wm.freight_usd_lb,
                atlanta_landed_usd_lb=round(landed_cost(r.spot_price_usd_lb, wm.freight_usd_lb, fee), 4),
                bags_available=r.bags_available,
                raw_url=r.raw_url,
                offer_key=offer_key(r, wm.warehouse),
            )
        )
        n += 1
    session.commit()
    return n


def run_scrapers(session: Session, only: list[str] | None = None, dry_run: bool = False) -> dict[str, Any]:
    classes = [SCRAPERS[k] for k in only if k in SCRAPERS] if only else enabled_scrapers(config.ENABLED_SCRAPERS)
    results: dict[str, Any] = {}
    for cls in classes:
        scraper = cls()
        logger = None if dry_run else RunLogger(session, f"scrape:{cls.key}")
        try:
            records = scraper.fetch()
            if dry_run:
                results[cls.key] = {"status": "ok", "rows": len(records),
                                    "sample": [r.to_dict() for r in records[:5]]}
                continue
            n = persist_offers(session, records, source=cls.key)
            status = "ok" if n else "partial"
            logger.finish(status, n, "" if n else "no offers parsed")
            results[cls.key] = {"status": status, "rows": n}
        except Exception as e:  # noqa: BLE001
            log.warning("scraper %s failed: %s", cls.key, e)
            if logger:
                logger.finish("error", 0, str(e))
            results[cls.key] = {"status": "error", "message": str(e)}
        finally:
            scraper.close()
    return results


def _move(path: Path, sub: str) -> Path:
    dest_dir = path.parent / sub
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / path.name
    if dest.exists():
        dest = dest_dir / f"{path.stem}_{uuid.uuid4().hex[:6]}{path.suffix}"
    shutil.move(str(path), dest)
    return dest


def ingest_file(session: Session, path: Path, *, move: bool = True) -> dict[str, Any]:
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if session.exec(select(IngestedFile).where(IngestedFile.sha256 == sha)).first():
        if move:
            _move(path, "processed")
        return {"file": path.name, "status": "duplicate", "rows": 0}
    try:
        result = parse_spot_sheet(data, path.name, offer_date=None)
        importer = result.offers[0].importer if result.offers else None
        if not result.offers:
            raise ValueError("; ".join(result.warnings) or "no offers recognised")
        for o in result.offers:
            if o.offer_date is None:
                o.offer_date = file_mtime_date(path)
        n = persist_offers(session, result.offers, source="dropzone", batch_id=sha[:12])
        session.add(IngestedFile(sha256=sha, filename=path.name, importer=importer, rows=n,
                                 status="ok", message=f"{result.method}; skipped {result.skipped}"))
        session.commit()
        if move:
            _move(path, "processed")
        return {"file": path.name, "status": "ok", "rows": n, "importer": importer,
                "method": result.method, "skipped": result.skipped}
    except Exception as e:  # noqa: BLE001
        session.rollback()
        session.add(IngestedFile(sha256=sha, filename=path.name, rows=0, status="error", message=str(e)[:2000]))
        session.commit()
        if move:
            failed = _move(path, "failed")
            failed.with_suffix(failed.suffix + ".error.txt").write_text(str(e), encoding="utf-8")
        return {"file": path.name, "status": "error", "message": str(e)}


def process_dropzone(session: Session, folder: Path | None = None) -> dict[str, Any]:
    folder = folder or config.SPOT_SHEETS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    files = list(iter_sheet_files(folder))
    if not files:
        return {"status": "ok", "files": []}
    logger = RunLogger(session, "dropzone")
    out = [ingest_file(session, p) for p in files]
    rows = sum(r.get("rows", 0) for r in out)
    errors = [r for r in out if r["status"] == "error"]
    status = "error" if errors and not rows else ("partial" if errors else "ok")
    logger.finish(status, rows, "; ".join(f"{e['file']}: {e['message']}" for e in errors))
    return {"status": status, "files": out}


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def save_upload(filename: str, data: bytes, folder: Path | None = None) -> Path:
    folder = folder or config.SPOT_SHEETS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    name = _SAFE.sub("_", Path(filename).name).strip("._") or "upload.csv"
    if Path(name).suffix.lower() not in {".csv", ".tsv", ".xlsx", ".xlsm", ".xls", ".pdf"}:
        raise ValueError("only CSV, TSV, XLSX/XLS or PDF spot sheets are accepted")
    dest = folder / name
    if dest.exists():
        dest = folder / f"{dest.stem}_{uuid.uuid4().hex[:6]}{dest.suffix}"
    dest.write_bytes(data)
    return dest
