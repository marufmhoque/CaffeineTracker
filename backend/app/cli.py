"""Command line: python -m backend.app.cli <command>

  init                         create the database and seed countries
  benchmarks                   Tier 1: ICE + FX (+ ICO CSV)
  market                       ICE forward curve + certified stocks CSV
  weather                      Open-Meteo growing-belt anomalies
  usda                         USDA FAS PSD crop balance sheets
  trade [--months N]           Tier 3: Comtrade / Census unit values
  scrape [--only k1,k2] [--dry-run]   Tier 2: importer scrapers
  portals                      Tier 2: authenticated portal downloads (Playwright)
  dropzone                     ingest files in data/spot_sheets/
  ingest FILE                  ingest one spot sheet (copied into the dropzone)
  alerts [--no-deliver]        run alert engine and print the digest
  digest                       print a digest of unread alerts
  all                          run every pipeline, then alerts
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from sqlmodel import select

from .alerts import build_digest
from .analytics import benchmark_summary
from .db import init_db, session_scope
from .jobs import job_alerts, job_benchmarks, job_dropzone, job_market, job_portals, job_usda, job_weather, run_all
from .models import Alert
from .pipelines import spot, trade


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="originpulse", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("benchmarks")
    sub.add_parser("market")
    sub.add_parser("weather")
    sub.add_parser("usda")
    t = sub.add_parser("trade")
    t.add_argument("--months", type=int)
    s = sub.add_parser("scrape")
    s.add_argument("--only")
    s.add_argument("--dry-run", action="store_true")
    sub.add_parser("portals")
    sub.add_parser("dropzone")
    i = sub.add_parser("ingest")
    i.add_argument("file")
    a = sub.add_parser("alerts")
    a.add_argument("--no-deliver", action="store_true")
    sub.add_parser("digest")
    sub.add_parser("all")
    args = p.parse_args(argv)

    init_db()
    if args.cmd == "init":
        print("database ready")
    elif args.cmd == "benchmarks":
        _print(job_benchmarks())
    elif args.cmd == "market":
        _print(job_market())
    elif args.cmd == "weather":
        _print(job_weather())
    elif args.cmd == "usda":
        _print(job_usda())
    elif args.cmd == "trade":
        with session_scope() as sess:
            _print(trade.run(sess, months=args.months))
    elif args.cmd == "scrape":
        only = [k.strip() for k in args.only.split(",")] if args.only else None
        with session_scope() as sess:
            _print(spot.run_scrapers(sess, only=only, dry_run=args.dry_run))
    elif args.cmd == "portals":
        _print(job_portals())
    elif args.cmd == "dropzone":
        _print(job_dropzone())
    elif args.cmd == "ingest":
        src = Path(args.file)
        dest = spot.save_upload(src.name, src.read_bytes())
        with session_scope() as sess:
            _print(spot.ingest_file(sess, dest))
    elif args.cmd == "alerts":
        res = job_alerts(deliver=not args.no_deliver)
        print(res.get("digest", res))
        if res.get("delivery"):
            _print(res["delivery"])
    elif args.cmd == "digest":
        with session_scope() as sess:
            unread = sess.exec(select(Alert).where(Alert.is_read.is_(False)).order_by(Alert.created_at)).all()
            print(build_digest(list(unread), benchmark_summary(sess)))
    elif args.cmd == "all":
        res = run_all()
        digest = res.get("alerts", {}).get("digest")
        _print({k: v for k, v in res.items() if k != "alerts"})
        if digest:
            print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
