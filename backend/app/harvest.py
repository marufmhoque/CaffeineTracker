"""Harvest calendar status per origin."""

from __future__ import annotations

from datetime import date

IN_HARVEST = "In Harvest"
FLY_CROP = "Fly Crop"
SHIPPING = "Main Crop Shipping"
OFF_SEASON = "Off-Season"

# Months after the main harvest ends during which the new crop is typically being exported/landing.
SHIPPING_WINDOW_MONTHS = 4


def _shipping_months(main_months: list[int]) -> set[int]:
    if not main_months:
        return set()
    end = main_months[-1]  # lists are stored in chronological order, possibly wrapping the year
    months = {((end - 1 + i) % 12) + 1 for i in range(1, SHIPPING_WINDOW_MONTHS + 1)}
    return months - set(main_months)


def harvest_status(
    main_months: list[int], fly_months: list[int], on: date | None = None
) -> str:
    month = (on or date.today()).month
    if month in main_months:
        return IN_HARVEST
    if month in fly_months:
        return FLY_CROP
    if month in _shipping_months(main_months):
        return SHIPPING
    return OFF_SEASON
