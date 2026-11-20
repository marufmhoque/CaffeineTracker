import io
import json
from datetime import date

import pandas as pd
import pytest

from backend.app.pipelines.benchmarks import parse_yahoo_chart
from backend.app.pipelines.trade import parse_census_rows, parse_comtrade_rows
from scrapers.extract import detect_process, detect_score, detect_variety
from scrapers.shopify import choose_variant, parse_product
from scrapers.spot_sheets import importer_from_filename, map_headers, parse_spot_sheet

from .conftest import FIXTURES


# ---------------------------------------------------------------- spot sheets

def test_fixture_spot_sheet_parses_every_live_lot():
    p = FIXTURES / "cafe-imports_2026-11-14.csv"
    r = parse_spot_sheet(p.read_bytes(), p.name)
    by = {o.lot_name: o for o in r.offers}
    assert len(r.offers) == 7
    assert r.skipped == 1  # the SOLD lot
    assert {o.importer for o in r.offers} == {"Cafe Imports"}
    assert {o.offer_date for o in r.offers} == {date(2026, 11, 14)}

    yirg = by["Yirgacheffe Konga Gr1"]
    assert (yirg.country_iso, yirg.process, yirg.sca_score, yirg.bag_weight_kg, yirg.spot_price_usd_lb, yirg.bags_available) == (
        "ETH", "Washed", 88.5, 60.0, 5.10, 42,
    )
    # Section-row context ("COLOMBIA") must not override a lot that names its own origin.
    assert by["Brazil Cerrado Mineiro NY2 17/18"].country_iso == "BRA"
    assert by["Huila Excelso EP"].country_iso == "COL"
    # 132 lb bag -> kg
    assert by["Brazil Cerrado Mineiro NY2 17/18"].bag_weight_kg == pytest.approx(59.87, abs=0.01)
    assert by["Pink Bourbon Lot 7"].process == "Anaerobic"
    assert by["Sumatra Gayo Grade 1 Triple Pick"].process == "Wet-Hulled"


def test_per_kg_and_per_bag_price_columns():
    csv = (
        "Origin,Description,Process,Bag,Warehouse,Price USD/kg\n"
        "Kenya,Nyeri AB,Washed,60 kg,Savannah,13.23\n"
    )
    r = parse_spot_sheet(csv.encode(), "atlas_2026-11-01.csv")
    assert r.offers[0].spot_price_usd_lb == pytest.approx(13.23 / 2.2046226, rel=1e-4)
    assert r.offers[0].importer == "Atlas Coffee Importers"

    csv = "Country,Lot,Bag size,Price per bag\nGuatemala,Huehue SHB,69,608.48\n"
    r = parse_spot_sheet(csv.encode(), "ally-coffee.csv")
    assert r.offers[0].spot_price_usd_lb == pytest.approx(4.0, rel=1e-3)
    assert r.offers[0].bag_weight_kg == 69


def test_xlsx_spot_sheet(tmp_path):
    df = pd.DataFrame(
        [["Peru", "Cajamarca FT/O", "Washed", 85, "69 kg", "Charleston", "$4.15", 80]],
        columns=["Country", "Lot", "Process", "Score", "Bag", "Warehouse", "Price/lb", "Bags available"],
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    r = parse_spot_sheet(buf.getvalue(), "sucafina_20261102.xlsx")
    o = r.offers[0]
    assert (o.importer, o.country_iso, o.spot_price_usd_lb, o.bags_available, o.offer_date) == (
        "Sucafina", "PER", 4.15, 80, date(2026, 11, 2),
    )


def test_header_mapping_prefers_specific_price_columns():
    m = map_headers(["Lot", "Price $/kg", "Price", "Bags"])
    assert m["price_kg"] == 1 and m["price_lb"] == 2 and m["bags"] == 3


def test_importer_from_filename():
    assert importer_from_filename("Cafe_Imports_Spot_Nov.pdf") == "Cafe Imports"
    assert importer_from_filename("ofi-specialty-offerlist.xlsx") == "Olam Specialty (ofi)"
    assert importer_from_filename("random.csv") is None


def test_unparseable_sheet_reports_warning():
    r = parse_spot_sheet(b"just,some\nrandom,text\n", "x.csv")
    assert not r.offers and r.warnings


@pytest.mark.parametrize(
    "text,process",
    [
        ("Ethiopia Guji Natural", "Natural"),
        ("Costa Rica Red Honey", "Honey"),
        ("Brazil Pulped Natural", "Honey"),
        ("Colombia Anaerobic Natural", "Anaerobic"),
        ("Colombia Swiss Water Decaf", "Decaf"),
        ("Sumatra Wet-Hulled", "Wet-Hulled"),
        ("Kenya AA Fully Washed", "Washed"),
        ("Mystery lot", None),
    ],
)
def test_detect_process(text, process):
    assert detect_process(text) == process


def test_detect_score_and_variety():
    assert detect_score("Cupping score: 87.5 — sweet") == 87.5
    assert detect_score("88 pts") == 88
    assert detect_score("Grown at 1900 masl") is None
    assert detect_variety("Geisha & Pink Bourbon") == "Pink Bourbon, Gesha"
    assert detect_variety("SL-28 and SL34") == "SL28, SL34"


# ---------------------------------------------------------------- shopify

PRODUCT = {
    "title": "Ethiopia Guji Hambela Natural",
    "handle": "ethiopia-guji-hambela-natural",
    "product_type": "Green Coffee",
    "tags": ["Process: Natural", "Score: 88.25", "Region: Guji"],
    "body_html": "<p>Blueberry, jasmine. Ships from our Oakland warehouse. Varieties: 74158.</p>",
    "variants": [
        {"title": "1 lb", "price": "9.50", "grams": 454, "available": True},
        {"title": "22 lb box", "price": "121.00", "grams": 9979, "available": True},
        {"title": "60 kg bag", "price": "560.00", "grams": 60000, "available": False},
    ],
}


def test_shopify_product_uses_largest_available_pack():
    rec = parse_product(PRODUCT, importer="Royal", base_url="https://example.com", default_warehouse=None)
    assert rec is not None
    assert rec.country_iso == "ETH"
    assert rec.process == "Natural"
    assert rec.sca_score == 88.25
    assert rec.origin_region == "Guji"
    assert rec.warehouse_location == "Oakland, CA"
    assert rec.bag_weight_kg == pytest.approx(9.979)
    assert rec.spot_price_usd_lb == pytest.approx(121.0 / (9.979 * 2.2046226), rel=1e-4)
    assert rec.raw_url == "https://example.com/products/ethiopia-guji-hambela-natural"


def test_shopify_skips_equipment_and_sold_out():
    gear = {**PRODUCT, "title": "Kaffelogic Roaster", "variants": PRODUCT["variants"]}
    assert parse_product(gear, importer="x", base_url="", default_warehouse=None) is None
    sold = {**PRODUCT, "variants": [{**v, "available": False} for v in PRODUCT["variants"]]}
    assert parse_product(sold, importer="x", base_url="", default_warehouse=None) is None
    assert choose_variant([{"title": "Sample", "price": "0", "grams": 0}]) is None


# ---------------------------------------------------------------- benchmarks / trade

def test_parse_yahoo_chart():
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": -18000},
                    "timestamp": [1763355600, 1763442000, 1763528400],
                    "indicators": {"quote": [{"close": [281.0, None, 275.5]}]},
                }
            ]
        }
    }
    rows = parse_yahoo_chart(payload)
    assert [v for _, v in rows] == [281.0, 275.5]
    with pytest.raises(ValueError):
        parse_yahoo_chart({"chart": {"result": [], "error": "nope"}})


def test_parse_comtrade_skips_aggregates_and_estimated_weights():
    rows = [
        {"partnerCode": 0, "period": "202608", "primaryValue": 1e9, "netWgt": 1e8},  # World
        {"partnerCode": 170, "period": "202608", "primaryValue": 7_400_000, "netWgt": 1_000_000},
        {"partnerCode": 76, "period": "202608", "primaryValue": 6_000_000, "netWgt": 1_000_000, "isNetWgtEstimated": True},
        {"partnerCode": 490, "period": "202608", "primaryValue": 50_000, "netWgt": 1_000},  # Taiwan alias
        {"partnerCode": 404, "period": "2025", "freqCode": "A", "primaryValue": 9_000_000, "netWgt": 1_000_000},
    ]
    out = parse_comtrade_rows(rows, {170: "COL", 76: "BRA", 404: "KEN"})
    by = {o["country_iso"]: o for o in out}
    assert set(by) == {"COL", "TWN", "KEN"}
    assert by["COL"]["unit_price_usd_lb"] == pytest.approx(7.4 / 2.2046226, rel=1e-4)
    assert by["KEN"]["freq"] == "A" and by["KEN"]["period_month"] == date(2025, 1, 1)


def test_parse_census_rows_sums_hs10_lines():
    table = [
        ["CTY_CODE", "CTY_NAME", "I_COMMODITY", "GEN_VAL_MO", "GEN_QY1_MO", "UNIT_QY1", "time"],
        ["3010", "COLOMBIA", "0901110015", "5000000", "700000", "KG", "2026-08"],
        ["3010", "COLOMBIA", "0901110025", "2000000", "300000", "KG", "2026-08"],
        ["0003", "EUROPEAN UNION", "0901110015", "1", "1", "KG", "2026-08"],
        ["-", "TOTAL FOR ALL COUNTRIES", "0901110015", "9", "9", "KG", "2026-08"],
    ]
    out = parse_census_rows(table, date(2026, 8, 1))
    assert len(out) == 1
    assert out[0]["country_iso"] == "COL"
    assert out[0]["import_value_usd"] == 7_000_000
    assert out[0]["unit_price_usd_lb"] == pytest.approx(7.0 / 2.2046226, rel=1e-4)
