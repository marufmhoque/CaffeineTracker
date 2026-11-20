"""Species tagging, schema migration, market signals (curve, stocks, weather, USDA) and the new cost terms."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from backend.app import analytics, costing, signals
from backend.app.db import get_engine, init_db
from backend.app.models import CertifiedStock, CropEstimate, FuturesQuote, SpotOffer, WeatherSignal
from backend.app.pipelines import market, usda, weather
from backend.app.pipelines.benchmarks import store_series
from backend.app.pipelines.spot import persist_offers
from backend.app.species import infer_species, matches
from scrapers.base import OfferRecord


# ---------------------------------------------------------------- species

@pytest.mark.parametrize("texts,mix,expected", [
    (("Vietnam Fine Robusta Grade 1",), "Robusta/Arabica", "Robusta"),
    (("Brazil Conilon Espirito Santo",), "Arabica/Robusta", "Robusta"),
    (("Uganda Bugisu AA", "SL28"), "Robusta/Arabica", "Arabica"),
    (("Ethiopia Guji",), "Arabica", "Arabica"),  # single-species origin fallback
    (("Mexico Chiapas",), "Arabica/Robusta", None),  # mixed origin, no clue
    (("Philippines Barako Liberica",), "Liberica/Robusta", "Liberica"),
])
def test_infer_species(texts, mix, expected):
    assert infer_species(*texts, species_mix=mix) == expected


def test_species_matches():
    assert matches(None, "Robusta")
    assert matches("robusta", "Robusta")
    assert not matches("Arabica", "Robusta")
    assert matches("Robusta", None, "Arabica/Robusta")


def test_migration_adds_species_column_and_backfills(tmp_path):
    engine = get_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as conn:  # a pre-species spot_offers table holding one robusta lot
        conn.execute(text(
            "CREATE TABLE spot_offers (id INTEGER PRIMARY KEY, scraped_at DATETIME, batch_id VARCHAR, source VARCHAR,"
            " importer VARCHAR, offer_date DATE, country_iso VARCHAR, lot_name VARCHAR, origin_region VARCHAR,"
            " process VARCHAR, variety VARCHAR, sca_score FLOAT, bag_weight_kg FLOAT, warehouse_location VARCHAR,"
            " warehouse_tier VARCHAR, is_southeast BOOLEAN, spot_price_usd_lb FLOAT, freight_usd_lb FLOAT,"
            " atlanta_landed_usd_lb FLOAT, bags_available INTEGER, raw_url VARCHAR, offer_key VARCHAR)"))
        conn.execute(text(
            "INSERT INTO spot_offers (scraped_at, batch_id, source, importer, lot_name, country_iso, is_southeast,"
            " spot_price_usd_lb, offer_key) VALUES ('2026-11-01', 'b', 's', 'I', 'Uganda Robusta Screen 18', NULL,"
            " 0, 2.5, 'k')"))
    init_db(engine)
    with Session(engine) as s:
        assert s.exec(select(SpotOffer)).one().species == "Robusta"
    init_db(engine)  # idempotent
    engine.dispose()


def test_persist_offers_tags_species_and_matrix_filters(session, rules):
    store_series(session, arabica_cents=[(date.today() - timedelta(days=1), 280.0)],
                 robusta_usd_t=[(date.today() - timedelta(days=1), 4400.0)])
    recs = [
        OfferRecord(importer="X", lot_name="Vietnam Fine Robusta", spot_price_usd_lb=2.6, country_iso="VNM"),
        OfferRecord(importer="X", lot_name="Vietnam Da Lat Catimor", spot_price_usd_lb=4.6, country_iso="VNM"),
        OfferRecord(importer="X", lot_name="Ethiopia Yirgacheffe", spot_price_usd_lb=5.0, country_iso="ETH"),
    ]
    persist_offers(session, recs, source="t", rules=rules)
    tags = {o.lot_name: o.species for o in session.exec(select(SpotOffer)).all()}
    assert tags == {"Vietnam Fine Robusta": "Robusta", "Vietnam Da Lat Catimor": "Arabica",
                    "Ethiopia Yirgacheffe": "Arabica"}

    rob = analytics.country_matrix(session, species="robusta")
    isos = {r["iso_code"] for r in rob["countries"]}
    assert "VNM" in isos and "ETH" not in isos
    vnm = next(r for r in rob["countries"] if r["iso_code"] == "VNM")
    assert vnm["spot_avg_usd_lb"] == pytest.approx(2.6)
    assert rob["benchmark"] == "robusta"
    assert vnm["diff_vs_c_usd_lb"] == pytest.approx(2.6 - 4400 / 2204.62262, abs=1e-3)

    arab = analytics.country_matrix(session, species="Arabica")
    assert next(r for r in arab["countries"] if r["iso_code"] == "VNM")["spot_avg_usd_lb"] == pytest.approx(4.6)
    assert len(analytics.current_offers(session, species="Robusta")) == 1


# ---------------------------------------------------------------- forward curve

def test_arabica_contracts_roll_through_delivery_months():
    c = market.arabica_contracts(date(2026, 11, 20), n=4)
    assert [s for s, _ in c] == ["KCZ26.NYB", "KCH27.NYB", "KCK27.NYB", "KCN27.NYB"]
    assert c[1][1] == date(2027, 3, 1)


def _q(month, px):
    return {"contract_month": month, "usd_lb": px}


def test_term_structure():
    back = market.term_structure([_q(date(2026, 12, 1), 2.80), _q(date(2027, 12, 1), 2.50)])
    assert back["state"] == "Backwardation" and back["slope_pct"] == pytest.approx(-10.71, abs=0.01)
    assert market.term_structure([_q(date(2026, 12, 1), 2.50), _q(date(2027, 3, 1), 2.60)])["state"] == "Contango"
    assert market.term_structure([_q(date(2026, 12, 1), 2.50), _q(date(2027, 3, 1), 2.51)])["state"] == "Flat"
    assert market.term_structure([_q(date(2026, 12, 1), 2.50)]) is None


def test_forward_curve_read_side(session):
    d = date(2026, 11, 19)
    market.store_quotes(session, [
        FuturesQuote(date=d, symbol="KCH27.NYB", contract_month=date(2027, 3, 1), usd_lb=2.70),
        FuturesQuote(date=d, symbol="KCZ26.NYB", contract_month=date(2026, 12, 1), usd_lb=2.80),
    ])
    out = signals.forward_curve(session)
    assert [c["symbol"] for c in out["contracts"]] == ["KCZ26.NYB", "KCH27.NYB"]
    assert out["contracts"][0]["label"] == "Dec 26"
    assert out["structure"]["state"] == "Backwardation"


# ---------------------------------------------------------------- certified stocks / robusta CSV

def test_cert_stocks_csv_and_pinch(session):
    today = date(2026, 11, 20)
    lines = ["date,bags"]
    for i in range(0, 900, 30):  # ~2.5 years: high, then a steep drawdown to the low
        d = today - timedelta(days=900 - i)
        lines.append(f"{d.isoformat()},{'1,000,000' if i < 600 else 600000 - (i - 600) * 1000}")
    lines.append(f"{today.isoformat()},300000")
    rows = market.parse_cert_stocks_csv("\n".join(lines).replace("1,000,000", '"1,000,000"'))
    assert rows[0][1] == 1_000_000
    market.store_cert_stocks(session, rows)
    out = signals.certified_stocks(session, today)
    assert out["latest"]["bags"] == 300000
    assert out["pinch"] is True
    with pytest.raises(ValueError):
        market.parse_cert_stocks_csv("day,qty\n2026-01-01,5")


def test_market_csv_upload_api(tmp_env):
    from backend.app.main import app

    with TestClient(app) as c:
        r = c.post("/api/ingest/market-csv?kind=robusta",
                   files={"file": ("rob.csv", b"date,usd_tonne\n2026-11-18,4400\n2026-11-19,4500\n", "text/csv")})
        assert r.status_code == 200 and r.json()["rows"] == 2
        summ = c.get("/api/benchmarks/summary").json()
        assert summ["ice_robusta"]["usd_lb"] == pytest.approx(4500 / 2204.62262, rel=1e-4)
        bad = c.post("/api/ingest/market-csv?kind=robusta", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
        assert bad.status_code == 422
        assert c.post("/api/ingest/market-csv?kind=nope",
                      files={"file": ("x.csv", b"a", "text/csv")}).status_code == 422


# ---------------------------------------------------------------- weather

def _days(start, n, rain, tmax=25.0, tmin=12.0):
    return {start + timedelta(days=i): (rain, tmax, tmin) for i in range(n)}


def test_score_belt_deficit_heat_and_frost():
    today = date(2026, 11, 20)
    belt = {"species": "Arabica"}
    history = {}
    for k in range(1, 11):
        history.update(_days(date(today.year - k, 1, 1), 366, 4.0))  # ~360 mm per 90 days
    recent = _days(today - timedelta(days=92), 92, 1.0)  # ~90 mm -> 25% of normal
    recent.update(_days(today, 10, 0.0, tmin=0.5))  # frost forecast
    s = weather.score_belt(belt, recent, history, today)
    assert s["rain_pct_normal"] == pytest.approx(25, abs=1)
    assert s["frost_forecast_days"] == 10
    assert s["risk"] == "Critical"

    calm = weather.score_belt(belt, _days(today - timedelta(days=92), 102, 4.0), history, today)
    assert calm["risk"] == "Low" and calm["reasons"]

    hot = _days(today - timedelta(days=92), 102, 4.0, tmax=33.0)
    assert weather.score_belt(belt, hot, history, today)["hot_days_30d"] == 30
    assert weather.score_belt({"species": "Robusta"}, hot, history, today)["hot_days_30d"] == 0


def test_weather_run_with_mock_client(session, monkeypatch, tmp_path):
    import json

    (tmp_path / "growing_belts.json").write_text(json.dumps({"belts": [
        {"country_iso": "BRA", "belt": "Sul de Minas", "species": "Arabica", "lat": -21.5, "lon": -45.4}]}))
    today = date(2026, 11, 20)

    def payload(start, n):
        days = [(start + timedelta(days=i)).isoformat() for i in range(n)]
        return {"daily": {"time": days, "precipitation_sum": [3.0] * n, "temperature_2m_max": [26.0] * n,
                          "temperature_2m_min": [14.0] * n}}

    class Resp:
        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    def fake_get(client, url, params=None, **kw):
        if "archive" in url:
            return Resp(payload(date.fromisoformat(params["start_date"]), 3800))
        return Resp(payload(today - timedelta(days=92), 102))

    monkeypatch.setattr(weather, "get_with_retry", fake_get)
    res = weather.run(session, client=object(), today=today)
    assert res["status"] == "ok" and res["rows"] == 1
    row = session.exec(select(WeatherSignal)).one()
    assert row.risk == "Low" and row.rain_pct_normal == pytest.approx(100, abs=2)
    assert signals.weather_latest(session, "BRA")[0]["belt"] == "Sul de Minas"


# ---------------------------------------------------------------- USDA

PSD_CSV = """Commodity_Code,Commodity_Description,Country_Code,Country_Name,Market_Year,Calendar_Year,Month,Attribute_ID,Attribute_Description,Unit_ID,Unit_Description,Value
0711100,"Coffee, Green",BR,"Brazil",2024,2026,06,029,"Arabica Production",02,"(1000 60 KG BAGS)",44500
0711100,"Coffee, Green",BR,"Brazil",2025,2026,06,029,"Arabica Production",02,"(1000 60 KG BAGS)",38000
0711100,"Coffee, Green",BR,"Brazil",2026,2026,06,029,"Arabica Production",02,"(1000 60 KG BAGS)",47500
0711100,"Coffee, Green",BR,"Brazil",2024,2026,06,028,"Production",02,"(1000 60 KG BAGS)",65500
0711100,"Coffee, Green",BR,"Brazil",2025,2026,06,028,"Production",02,"(1000 60 KG BAGS)",63000
0711100,"Coffee, Green",BR,"Brazil",2026,2026,06,028,"Production",02,"(1000 60 KG BAGS)",71900
0711100,"Coffee, Green",BR,"Brazil",2026,2026,06,030,"Robusta Production",02,"(1000 60 KG BAGS)",24400
0711100,"Coffee, Green",VM,"Vietnam",2025,2026,06,028,"Production",02,"(1000 60 KG BAGS)",31700
0711100,"Coffee, Green",VM,"Vietnam",2026,2026,06,028,"Production",02,"(1000 60 KG BAGS)",32500
0711100,"Coffee, Green",VM,"Vietnam",2026,2026,06,030,"Robusta Production",02,"(1000 60 KG BAGS)",31400
0711100,"Coffee, Green",VM,"Vietnam",2026,2026,06,029,"Arabica Production",02,"(1000 60 KG BAGS)",1100
0711100,"Coffee, Green",US,"United States",2026,2026,06,125,"Domestic Consumption",02,"(1000 60 KG BAGS)",95000
0711100,"Coffee, Green",BR,"Brazil",2026,2026,06,125,"Domestic Consumption",02,"(1000 60 KG BAGS)",5000
0711100,"Coffee, Green",BR,"Brazil",2026,2026,06,176,"Ending Stocks",02,"(1000 60 KG BAGS)",1200
"""


def test_usda_parse_and_cycle(session):
    rows = usda.parse_psd(PSD_CSV.encode(), {"Brazil": "BRA"}, 2020)
    assert {r.country_iso for r in rows} == {"BRA", "VNM", "WLD"}  # US is not a producer in the seed
    usda.store(session, rows)
    usda.store(session, rows)  # upsert, no duplicates
    assert len(session.exec(select(CropEstimate)).all()) == len(rows)

    bra = usda.crop_cycle([r for r in rows if r.country_iso == "BRA"])
    assert bra["market_year"] == "2026/27" and bra["cycle"] == "On-year" and bra["cycle_basis"] == "Arabica crop"
    assert bra["yoy_pct"] == pytest.approx(14.1, abs=0.1)
    assert usda.crop_cycle([r for r in rows if r.country_iso == "VNM"])["cycle"] is None  # Robusta origin

    world = usda.world_balance([r for r in rows if r.country_iso == "WLD"])
    assert world["production_k_bags"] == 71900 + 32500
    assert world["consumption_k_bags"] == 100000
    assert world["surplus_pct"] == pytest.approx(4.4, abs=0.01)

    out = signals.crops(session)
    assert out["origins"][0]["iso_code"] == "BRA" and out["origins"][0]["name"] == "Brazil"


# ---------------------------------------------------------------- cost terms

def test_landed_cost_with_tariff_and_ocean(rules):
    assert costing.landed_cost(4.0, 0.15, 0.05, tariff_pct=0.10, ocean_usd_lb=0.03) == pytest.approx(4.63)
    per_lb = costing.container_to_usd_lb(4232.9)  # 320 x 60 kg = 42,329 lb
    assert per_lb == pytest.approx(0.10, rel=1e-3)
    bd, _ = costing.cost_breakdown(4.0, "Savannah", rules=rules, tariff_pct=0.1, ocean_usd_lb=0.05)
    assert bd.tariff_usd_lb == pytest.approx(0.4) and bd.landed_usd_lb == pytest.approx(4.0 + 0.4 + 0.05 + 0.15 + 0.05)
    bad = {**rules, "tariff_pct": -1}
    with pytest.raises(ValueError):
        costing.validate_rules(bad)


# ---------------------------------------------------------------- API

def test_signal_endpoints(tmp_env, rules):
    from backend.app.main import app

    with Session(tmp_env) as s:
        today = date.today()
        store_series(s, arabica_cents=[(today - timedelta(days=i), 280.0 + i % 7) for i in range(1, 2200)])
        persist_offers(s, [OfferRecord(importer="X", lot_name="Huila Pink Bourbon", spot_price_usd_lb=6.0,
                                       country_iso="COL", origin_region="Finca El Paraíso", bag_weight_kg=35,
                                       warehouse_location="Savannah")], source="t", rules=rules)
        s.add(WeatherSignal(as_of=today, country_iso="COL", belt="Huila", species="Arabica", lat=2.2, lon=-75.6,
                            risk="Moderate", reasons_json=["Rainfall 70% of normal (90 days)"]))
        s.add(CertifiedStock(date=today, bags=400000))
        s.commit()

    with TestClient(app) as c:
        five = c.get("/api/benchmarks/range?range=5y").json()
        assert five["interval"] == "weekly" and 250 <= len(five["points"]) <= 265
        assert c.get("/api/benchmarks/range?range=1m").json()["interval"] == "daily"
        assert len(c.get("/api/benchmarks/range?range=max").json()["points"]) > len(five["points"])
        assert c.get("/api/benchmarks/range?range=10y").status_code == 422

        sig = c.get("/api/signals").json()
        assert set(sig) == {"curve", "weather", "certified_stocks", "crops"}
        assert sig["weather"][0]["risk"] == "Moderate"

        d = c.get("/api/countries/col/detail?shrinkage=0.2").json()
        assert d["name"] == "Colombia" and d["weather_risk"] == "Moderate"
        lot = d["lots"][0]
        assert lot["producer"] == "Finca El Paraíso" and lot["species"] == "Arabica"
        assert lot["roasted_usd_lb"] == pytest.approx(lot["landed_usd_lb"] / 0.8, rel=1e-3)
        assert d["species_share"] == {"Arabica": 100}
        assert c.get("/api/countries/XXX/detail").status_code == 404

        assert c.get("/api/matrix?species=Robusta").json()["species"] == "Robusta"
        assert c.get("/api/matrix?species=bogus").status_code == 422
        assert c.get("/api/history?country=COL&range=5y").status_code == 200

        calc = c.post("/api/calc/landed", json={"price": 4.0, "unit": "lb", "warehouse": "Savannah",
                                                "tariff_pct": 10, "ocean_usd_container": 4232.9,
                                                "bag_kg": 60, "bags": 10}).json()
        assert calc["per_lb"]["tariff_usd_lb"] == pytest.approx(0.4)
        assert calc["per_lb"]["ocean_usd_lb"] == pytest.approx(0.1, rel=1e-3)
        assert calc["per_lb"]["landed_usd_lb"] == pytest.approx(4.0 + 0.4 + 0.1 + 0.15 + 0.05, rel=1e-4)
        assert calc["per_kg"]["tariff_pct"] == pytest.approx(0.1)
        assert calc["order"]["tariff_usd"] > 0

