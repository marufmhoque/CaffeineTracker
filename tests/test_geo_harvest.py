from datetime import date

import pytest

from backend.app.db import load_seed
from backend.app.geo import resolve_census_country, resolve_country
from backend.app.harvest import FLY_CROP, IN_HARVEST, OFF_SEASON, SHIPPING, harvest_status


def test_seed_covers_70_plus_countries():
    seed = load_seed()
    assert len(seed) >= 70
    assert len({c["iso_code"] for c in seed}) == len(seed)
    regions = {c["region"] for c in seed}
    assert regions == {
        "Central America & Mexico", "South America", "East & Central Africa", "West Africa", "Asia & Oceania", "Caribbean",
    }
    for c in seed:
        assert all(1 <= m <= 12 for m in c["main_harvest_months"] + c["fly_harvest_months"])
        assert c["default_bag_kg"] in (60, 69, 70)


@pytest.mark.parametrize(
    "text,iso",
    [
        ("Ethiopia Yirgacheffe Konga", "ETH"),
        ("Yirgacheffe Konga Gr1", "ETH"),
        ("Papua New Guinea Kimel Peaberry", "PNG"),
        ("Guinea Ziama Robusta", "GIN"),
        ("Equatorial Guinea", "GNQ"),
        ("Sumatra Gayo Grade 1", "IDN"),
        ("Nicaragua Java variety", "NIC"),  # 'Java' the variety shouldn't beat the named country
        ("DR Congo Kivu", "COD"),
        ("Congo Brazzaville lot", "COG"),
        ("Colombia Huila", "COL"),
        ("Côte d'Ivoire robusta", "CIV"),
        ("Burma Shan State", "MMR"),
        ("Jamaica Blue Mountain No.1", "JAM"),
        ("Brazil Cerrado Mineiro", "BRA"),
        ("Organic French Roast Blend", None),
    ],
)
def test_resolve_country(text, iso):
    assert resolve_country(text) == iso


def test_resolve_country_field_mode():
    assert resolve_country("PNG", field=True) == "PNG"
    assert resolve_country("eth", field=True) == "ETH"
    assert resolve_country("Java", field=True) == "IDN"


def test_census_names():
    assert resolve_census_country("BURMA") == "MMR"
    assert resolve_census_country("CONGO, DEMOCRATIC REPUBLIC OF THE (CONGO KINSHASA)") == "COD"
    assert resolve_census_country("COLOMBIA") == "COL"


def test_harvest_status():
    # Colombia: main Sep–Dec, mitaca Apr–Jun
    main, fly = [9, 10, 11, 12], [4, 5, 6]
    assert harvest_status(main, fly, date(2026, 10, 1)) == IN_HARVEST
    assert harvest_status(main, fly, date(2026, 5, 1)) == FLY_CROP
    assert harvest_status(main, fly, date(2026, 2, 1)) == SHIPPING
    assert harvest_status(main, fly, date(2026, 8, 1)) == OFF_SEASON
    # Ethiopia wraps the year: Oct–Feb, shipping Mar–Jun
    eth = [10, 11, 12, 1, 2]
    assert harvest_status(eth, [], date(2026, 1, 15)) == IN_HARVEST
    assert harvest_status(eth, [], date(2026, 4, 15)) == SHIPPING
    assert harvest_status(eth, [], date(2026, 8, 15)) == OFF_SEASON
