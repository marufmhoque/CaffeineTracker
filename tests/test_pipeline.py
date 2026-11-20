from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.app import analytics
from backend.app.alerts import (
    FX_TRIGGER,
    ORIGIN_DROP,
    SE_ARRIVAL,
    VALUE_DEAL,
    build_digest,
    run_alert_engine,
)
from backend.app.models import MarketBenchmark, SpotOffer
from backend.app.pipelines.benchmarks import store_series
from backend.app.pipelines.spot import ingest_file, persist_offers, save_upload
from scrapers.base import OfferRecord

from .conftest import FIXTURES

NOW = datetime.now(timezone.utc)


def _offer(lot, price, wh="Oakland, CA", score=None, process="Washed", iso="ETH"):
    return OfferRecord(importer="Test Imports", lot_name=lot, spot_price_usd_lb=price, country_iso=iso,
                       process=process, sca_score=score, warehouse_location=wh, bag_weight_kg=60)


def _batch(session, records, when, batch_id, rules):
    persist_offers(session, records, source="test", batch_id=batch_id, rules=rules)
    for o in session.exec(select(SpotOffer).where(SpotOffer.batch_id == batch_id)).all():
        o.scraped_at = when
        session.add(o)
    session.commit()


def test_persist_offers_computes_landed_cost(session, rules):
    persist_offers(session, [_offer("A", 4.10, wh="Savannah GA")], source="t", rules=rules)
    o = session.exec(select(SpotOffer)).one()
    assert o.is_southeast and o.warehouse_location == "Savannah, GA"
    assert o.freight_usd_lb == pytest.approx(0.15)
    assert o.atlanta_landed_usd_lb == pytest.approx(4.30)


def test_alert_engine_end_to_end(session, rules):
    _batch(session, [_offer("Lot 1", 5.0), _offer("Lot 2", 5.2), _offer("Lot 3", 5.4)],
           NOW - timedelta(days=15), "b1", rules)
    _batch(
        session,
        [
            _offer("Lot 1", 4.6),
            _offer("Lot 2", 4.7),
            _offer("Lot 3", 4.8),
            _offer("Konga 88", 4.4, wh="Savannah, GA", score=88.0),
        ],
        NOW - timedelta(hours=6),
        "b2",
        rules,
    )
    # FX: BRL from 5.00 to 5.30 per USD within a week (+6%).
    today = date.today()
    fx = [(today - timedelta(days=d), 5.0) for d in range(14, 7, -1)] + [(today - timedelta(days=d), 5.3) for d in (3, 2, 1)]
    store_series(session, fx={"BRL": fx})

    alerts = run_alert_engine(session)
    types = {a.alert_type for a in alerts}
    assert types == {ORIGIN_DROP, VALUE_DEAL, SE_ARRIVAL, FX_TRIGGER}
    drop = next(a for a in alerts if a.alert_type == ORIGIN_DROP)
    assert drop.country_iso == "ETH" and drop.data_json["pct"] < -5
    deal = next(a for a in alerts if a.alert_type == VALUE_DEAL)
    assert "Konga 88" in deal.message
    se = [a for a in alerts if a.alert_type == SE_ARRIVAL]
    assert len(se) == 1 and "Savannah" in se[0].message
    fxa = next(a for a in alerts if a.alert_type == FX_TRIGGER)
    assert fxa.data_json["currency"] == "BRL" and fxa.country_iso == "BRA"

    # Re-running doesn't duplicate.
    assert run_alert_engine(session) == []
    digest = build_digest(alerts)
    assert "Origin price drops (1)" in digest and "FX triggers (1)" in digest


def test_first_ever_batch_is_not_an_arrival(session, rules):
    _batch(session, [_offer("Lot SE", 4.0, wh="Charleston")], NOW - timedelta(hours=2), "only", rules)
    assert not [a for a in run_alert_engine(session) if a.alert_type == SE_ARRIVAL]


def test_single_bad_fx_print_does_not_trigger(session):
    today = date.today()
    fx = [(today - timedelta(days=d), 7.40) for d in range(14, 0, -1)]
    fx[-1] = (fx[-1][0], 7.64)  # one noisy close
    store_series(session, fx={"GTQ": fx})
    assert not [a for a in run_alert_engine(session) if a.alert_type == FX_TRIGGER]


def test_matrix_and_history(session, rules):
    store_series(session, arabica_cents=[(date.today() - timedelta(days=1), 280.0)])
    _batch(session, [_offer("Lot 1", 4.80), _offer("Lot 2", 5.20)], NOW - timedelta(hours=1), "b", rules)
    m = analytics.country_matrix(session)
    eth = next(c for c in m["countries"] if c["iso_code"] == "ETH")
    assert eth["spot_avg_usd_lb"] == pytest.approx(5.0)
    assert eth["diff_vs_c_usd_lb"] == pytest.approx(5.0 - 2.80)
    assert eth["spot_offer_count"] == 2
    assert len(m["countries"]) >= 70
    h = analytics.history(session, "ETH", range_="30d")
    assert h["points"][-1]["spot_avg_usd_lb"] == pytest.approx(5.0)


def test_latest_trade_uses_primary_market_window(session):
    from backend.app.models import TradeValuation

    def tv(month, reporter, kg, usd_lb, freq="M"):
        return TradeValuation(period_month=month, freq=freq, country_iso="ECU", reporter=reporter,
                              import_volume_kg=kg, import_value_usd=usd_lb * kg * 2.2046226218,
                              unit_price_usd_lb=usd_lb)

    rows = [
        # Japan: big, steady volumes at ~$3.00-3.30
        *(tv(date(2026, m, 1), "JPN", 200_000, 3.0) for m in range(1, 6)),
        *(tv(date(2026, m, 1), "JPN", 200_000, 3.3) for m in range(6, 9)),
        *(tv(date(2025, m, 1), "JPN", 200_000, 2.5) for m in range(6, 9)),
        # Germany: one tiny, very expensive lot in the latest month — must not become the headline
        tv(date(2026, 9, 1), "DEU", 600, 19.46),
    ]
    session.add_all(rows)
    session.commit()
    t = analytics.latest_trade(session, today=date(2026, 11, 19))["ECU"]
    assert t["reporters"] == ["JPN"]
    assert t["period"] == "2026-08-01"
    assert t["unit_usd_lb"] == pytest.approx(3.3)
    assert t["chg_yoy_pct"] == pytest.approx(32.0)
    assert t["chg_mom_pct"] == pytest.approx((3.3 - 3.2) / 3.2 * 100, abs=0.01)


def test_spot_average_prefers_full_bags_over_small_packs(session, rules):
    box = _offer("Crown Jewel box", 12.0)
    box.bag_weight_kg = 9.98
    _batch(session, [box, _offer("Bag lot", 4.0), _offer("Bag lot 2", 5.0)], NOW - timedelta(hours=1), "mix", rules)
    eth = next(c for c in analytics.country_matrix(session)["countries"] if c["iso_code"] == "ETH")
    assert eth["spot_avg_usd_lb"] == pytest.approx(4.5)
    assert eth["spot_basis"] == "bags" and eth["spot_offer_count"] == 2

    only_box = _offer("Kenya box", 11.0, iso="KEN")
    only_box.bag_weight_kg = 9.98
    _batch(session, [only_box], NOW - timedelta(minutes=30), "box", rules)
    ken = next(c for c in analytics.country_matrix(session)["countries"] if c["iso_code"] == "KEN")
    assert ken["spot_avg_usd_lb"] == pytest.approx(11.0) and ken["spot_basis"] == "small packs"


def test_stale_batches_drop_out_of_current(session, rules):
    _batch(session, [_offer("Old", 4.0)], NOW - timedelta(days=analytics.STALE_DAYS + 2), "old", rules)
    assert analytics.current_offers(session) == []


def test_dropzone_ingest_moves_file_and_dedupes(tmp_env, session):
    data = (FIXTURES / "cafe-imports_2026-11-14.csv").read_bytes()
    p = save_upload("cafe-imports_2026-11-14.csv", data)
    res = ingest_file(session, p)
    assert res["status"] == "ok" and res["rows"] == 7
    assert not p.exists() and (p.parent / "processed" / p.name).exists()
    p2 = save_upload("copy.csv", data)
    assert ingest_file(session, p2)["status"] == "duplicate"


def test_save_upload_rejects_other_types(tmp_env):
    with pytest.raises(ValueError):
        save_upload("evil.exe", b"MZ")


def test_api(tmp_env):
    from backend.app.main import app

    with TestClient(app) as c:
        with open(FIXTURES / "cafe-imports_2026-11-14.csv", "rb") as f:
            r = c.post("/api/ingest/upload", files={"file": ("cafe-imports_2026-11-14.csv", f, "text/csv")})
        assert r.status_code == 200 and r.json()["rows"] == 7

        offers = c.get("/api/offers?southeast=true&shrinkage=0.2").json()
        assert offers["shrinkage"] == 0.2
        assert offers["offers"] and all(o["is_southeast"] for o in offers["offers"])
        o = offers["offers"][0]
        assert o["roasted_usd_lb"] == pytest.approx(o["landed_usd_lb"] / 0.8, rel=1e-3)

        calc = c.post("/api/calc/landed", json={"price": 608.48, "unit": "bag", "bag_kg": 69, "warehouse": "Savannah", "bags": 10}).json()
        assert calc["per_lb"]["spot_usd_lb"] == pytest.approx(4.0, rel=1e-3)
        assert calc["per_lb"]["landed_usd_lb"] == pytest.approx(4.2, rel=1e-3)
        assert calc["order"]["green_lb"] == pytest.approx(1521.2, abs=0.1)
        assert c.post("/api/calc/landed", json={"price": 600, "unit": "bag"}).status_code == 422

        rules = c.get("/api/settings/freight").json()
        rules["tiers"][0]["default"] = 99
        assert c.put("/api/settings/freight", json=rules).status_code == 422

        assert c.get("/api/history?country=XXX").status_code == 404
        assert len(c.get("/api/countries").json()) >= 70
        assert c.post("/api/ingest/email?token=nope").status_code == 403
        assert c.post("/api/ingest/run/bogus").status_code == 404
