"""Optional: extract spot-sheet offers from a PDF with Claude when table parsing finds nothing.

Enabled only when ANTHROPIC_API_KEY is set and the `anthropic` package is installed
(`pip install -r backend/requirements-optional.txt`). Uses structured outputs so the response is
guaranteed to match OFFER_SCHEMA, and server-side refusal fallbacks.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import date

from backend.app import config
from backend.app.geo import resolve_country
from backend.app.units import normalize_to_usd_lb

from .base import OfferRecord
from .extract import normalize_process

log = logging.getLogger(__name__)


def _nullable(t: str) -> dict:
    return {"anyOf": [{"type": t}, {"type": "null"}]}


OFFER_SCHEMA = {
    "type": "object",
    "properties": {
        "offers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "country": {"type": "string"},
                    "lot_name": {"type": "string"},
                    "region": _nullable("string"),
                    "process": _nullable("string"),
                    "variety": _nullable("string"),
                    "sca_score": _nullable("number"),
                    "bag_weight_kg": _nullable("number"),
                    "warehouse": _nullable("string"),
                    "price": {"type": "number"},
                    "price_unit": {"type": "string", "enum": ["lb", "kg", "bag"]},
                    "bags_available": _nullable("integer"),
                },
                "required": ["country", "lot_name", "region", "process", "variety", "sca_score",
                             "bag_weight_kg", "warehouse", "price", "price_unit", "bags_available"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["offers"],
    "additionalProperties": False,
}

PROMPT = (
    "This PDF is a green coffee importer's spot-offer sheet. Extract every coffee lot offered for sale "
    "as one entry. Use the price exactly as printed and set price_unit to what it is quoted per "
    "(lb, kg, or a whole bag). bag_weight_kg is the bag/box size in kg (convert lb to kg). warehouse is "
    "the US warehouse or port the lot sits in, if stated. Use null for anything not printed on the "
    "sheet; do not guess. Skip sold-out lots, roasted coffee and non-coffee items."
)


def available() -> bool:
    if not config.ANTHROPIC_API_KEY:
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def extract_offers_from_pdf(
    data: bytes, *, filename: str, importer: str | None, offer_date: date | None
) -> list[OfferRecord]:
    import anthropic

    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=config.ANTHROPIC_MODEL,
        max_tokens=64000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": OFFER_SCHEMA},
        },
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.standard_b64encode(data).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise RuntimeError(f"LLM extraction declined for {filename}")
    if message.stop_reason == "max_tokens":
        raise RuntimeError(f"LLM extraction output truncated for {filename}; split the PDF")
    text = next((b.text for b in message.content if b.type == "text"), "")
    payload = json.loads(text)

    offers: list[OfferRecord] = []
    for o in payload.get("offers", []):
        iso = resolve_country(o.get("country"), field=True) or resolve_country(o.get("lot_name"))
        price_lb = normalize_to_usd_lb(float(o["price"]), o["price_unit"], o.get("bag_weight_kg"))
        if not iso or not price_lb or price_lb <= 0:
            continue
        offers.append(
            OfferRecord(
                importer=importer or "Unknown importer",
                lot_name=o["lot_name"],
                spot_price_usd_lb=round(price_lb, 4),
                country_iso=iso,
                offer_date=offer_date,
                origin_region=o.get("region"),
                process=normalize_process(o.get("process")),
                variety=o.get("variety"),
                sca_score=o.get("sca_score"),
                bag_weight_kg=o.get("bag_weight_kg"),
                warehouse_location=o.get("warehouse"),
                bags_available=o.get("bags_available"),
                raw_url=filename,
                extra={"extracted_by": message.model},
            )
        )
    return offers
