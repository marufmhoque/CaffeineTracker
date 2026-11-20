import pytest

from backend.app.units import (
    LB_PER_KG,
    bag_price_to_usd_lb,
    cents_lb_to_usd_lb,
    convert_price,
    normalize_to_usd_lb,
    parse_price,
    parse_weight_kg,
    usd_kg_to_usd_lb,
    usd_lb_to_bag_price,
    usd_lb_to_usd_kg,
    usd_tonne_to_usd_lb,
)


def test_lb_kg_roundtrip():
    assert usd_lb_to_usd_kg(1.0) == pytest.approx(2.20462262)
    assert usd_kg_to_usd_lb(usd_lb_to_usd_kg(4.25)) == pytest.approx(4.25)
    assert convert_price(4.0, "lb", "kg") == pytest.approx(8.81849)
    assert convert_price(8.81849, "kg", "lb") == pytest.approx(4.0, abs=1e-5)
    assert convert_price(None, "lb", "kg") is None
    with pytest.raises(ValueError):
        convert_price(1, "lb", "bag")


@pytest.mark.parametrize(
    "bag_kg,lbs",
    [(60, 132.277), (69, 152.119), (70, 154.324)],
)
def test_standard_bag_weights(bag_kg, lbs):
    # A $100/bag price -> $/lb uses the bag's true lb weight.
    assert bag_price_to_usd_lb(100.0, bag_kg) == pytest.approx(100.0 / lbs, rel=1e-4)
    assert usd_lb_to_bag_price(bag_price_to_usd_lb(562.0, bag_kg), bag_kg) == pytest.approx(562.0)


def test_bag_price_rejects_zero_weight():
    with pytest.raises(ValueError):
        bag_price_to_usd_lb(100, 0)


def test_commodity_quote_units():
    assert cents_lb_to_usd_lb(275.5) == pytest.approx(2.755)
    # ICE Robusta USD/tonne -> USD/lb
    assert usd_tonne_to_usd_lb(4409.24) == pytest.approx(2.0, rel=1e-4)


@pytest.mark.parametrize(
    "text,kg",
    [
        ("69 kg", 69),
        ("70kg GrainPro", 70),
        ("60-kg bag", 60),
        ("30 kilos", 30),
        ("152 lb", 152 / LB_PER_KG),
        ("22 lb box", 22 / LB_PER_KG),
        ("1,000 lbs", 1000 / LB_PER_KG),
        ("Full bag", None),
        (None, None),
    ],
)
def test_parse_weight(text, kg):
    got = parse_weight_kg(text)
    if kg is None:
        assert got is None
    else:
        assert got == pytest.approx(kg, rel=1e-4)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$4.25", (4.25, "unknown")),
        ("4.25/lb", (4.25, "lb")),
        ("$9.37 / kg", (9.37, "kg")),
        ("USD 562.00 per bag", (562.0, "bag")),
        ("425¢/lb", (4.25, "lb")),
        ("$1,250.00/bag", (1250.0, "bag")),
        (3.5, (3.5, "unknown")),
        ("SOLD", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_price(text, expected):
    got = parse_price(text)
    if expected is None:
        assert got is None
    else:
        assert got[0] == pytest.approx(expected[0])
        assert got[1] == expected[1]


def test_normalize_to_usd_lb():
    assert normalize_to_usd_lb(4.0, "lb") == 4.0
    assert normalize_to_usd_lb(4.0, "unknown") == 4.0
    assert normalize_to_usd_lb(8.81849, "kg") == pytest.approx(4.0, abs=1e-5)
    assert normalize_to_usd_lb(608.48, "bag", 69) == pytest.approx(4.0, rel=1e-3)
    assert normalize_to_usd_lb(608.48, "bag", None) is None
