"""Parse importer spot-offer sheets (CSV / TSV / XLSX / PDF) into OfferRecords.

Spot sheets differ per importer, so parsing is heuristic:
- the header row is found by scoring rows against a synonym table (it need not be the first row);
- section rows that only name a country ("ETHIOPIA") set the origin for the rows below them;
- prices may be per lb, per kg or per bag; bag sizes in kg or lb;
- PDFs are read with pdfplumber's table extraction, falling back to Claude (if ANTHROPIC_API_KEY is set)
  when no table yields offers.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from backend.app.geo import resolve_country
from backend.app.units import (
    KG_PER_LB,
    STANDARD_BAG_KG,
    normalize_to_usd_lb,
    parse_price,
    parse_weight_kg,
)

from .base import OfferRecord
from .extract import (
    detect_process,
    detect_score,
    detect_variety,
    normalize_process,
    parse_int,
    parse_score,
)

log = logging.getLogger(__name__)

# (field, synonyms). Order matters: specific price fields before the generic "price".
FIELD_SYNONYMS: list[tuple[str, list[str]]] = [
    ("price_kg", ["$/kg", "usd/kg", "price/kg", "price per kg", "price kg", "kg price", "us$/kg"]),
    ("price_bag", ["$/bag", "usd/bag", "price/bag", "price per bag", "bag price", "price per box", "$/box"]),
    ("price_lb", ["$/lb", "usd/lb", "price/lb", "price per lb", "lb price", "us$/lb", "spot price",
                  "spot", "price", "fob", "ex-warehouse", "ex warehouse", "ex-wh", "cost"]),
    ("bags", ["bags available", "bags avail", "available bags", "avail bags", "bags", "available",
              "avail", "qty", "quantity", "inventory", "stock", "units available", "boxes"]),
    ("bag_size", ["bag size", "bag weight", "bag kg", "kg/bag", "bag type", "packaging", "pack size",
                  "bag", "size", "unit size", "weight", "pkg"]),
    ("country", ["country of origin", "country", "origin"]),
    ("region", ["region", "farm", "producer", "coop", "co-op", "cooperative", "washing station", "mill",
                "estate", "station", "department", "province", "area"]),
    ("lot", ["lot name", "lot", "description", "coffee", "name", "item", "marks", "product", "contract",
             "reference", "ico", "lot #", "lot number"]),
    ("process", ["process", "processing", "prep", "method", "preparation"]),
    ("variety", ["variety", "varietal", "varietals", "varieties", "cultivar"]),
    ("score", ["cupping score", "cup score", "sca score", "score", "sca", "points", "q score", "grade score"]),
    ("warehouse", ["warehouse", "whse", "location", "port", "ships from", "stored", "position", "wh",
                   "shipping point", "landed"]),
    ("importer", ["importer", "supplier", "vendor", "seller"]),
    ("offer_date", ["offer date", "date", "as of", "updated"]),
]

KNOWN_IMPORTERS = {
    "cafe imports": "Cafe Imports", "cafeimports": "Cafe Imports", "atlas": "Atlas Coffee Importers",
    "ally": "Ally Coffee", "olam": "Olam Specialty (ofi)", "ofi": "Olam Specialty (ofi)",
    "sucafina": "Sucafina", "royal": "Royal Coffee", "genuine origin": "Genuine Origin",
    "covoya": "Covoya", "interamerican": "InterAmerican Coffee", "balzac": "Balzac Brothers",
    "mercanta": "Mercanta", "osito": "Osito", "red fox": "Red Fox Coffee Merchants",
    "collaborative": "Collaborative Coffee Source", "coffee shrub": "Coffee Shrub", "bodhi": "Bodhi Leaf",
    "nordic": "Nordic Approach", "volcafe": "Volcafe Specialty", "falcon": "Falcon Specialty",
    "crop to cup": "Crop to Cup", "semente": "Semente", "nossa": "Nossa Familia",
}


def _norm_header(h: Any) -> str:
    s = str(h if h is not None else "").strip().lower()
    s = s.replace("\n", " ").replace("us $", "us$").replace("usd $", "$")
    s = re.sub(r"[()\[\]:#*]", " ", s)
    s = re.sub(r"\s*/\s*", "/", s)
    return re.sub(r"\s+", " ", s).strip()


def map_headers(headers: list[Any]) -> dict[str, int]:
    """Map logical fields -> column index. Exact synonym matches win over substring matches."""
    normed = [_norm_header(h) for h in headers]
    mapping: dict[str, int] = {}
    used: set[int] = set()
    for exact in (True, False):
        for fname, syns in FIELD_SYNONYMS:
            if fname in mapping:
                continue
            for syn in syns:
                hit = None
                for i, h in enumerate(normed):
                    if i in used or not h:
                        continue
                    if (h == syn) if exact else (len(syn) > 2 and re.search(r"(?<![a-z])" + re.escape(syn) + r"(?![a-z])", h)):
                        hit = i
                        break
                if hit is not None:
                    mapping[fname] = hit
                    used.add(hit)
                    break
    # A bare "price" header that says /kg or /bag in its text belongs to those fields.
    if "price_lb" in mapping and "price_kg" not in mapping and "kg" in normed[mapping["price_lb"]]:
        mapping["price_kg"] = mapping.pop("price_lb")
    return mapping


def _header_score(row: list[Any]) -> int:
    m = map_headers(row)
    has_price = any(k in m for k in ("price_lb", "price_kg", "price_bag"))
    return len(m) + (2 if has_price else -5)


def find_header_row(rows: list[list[Any]], scan: int = 25) -> int | None:
    best, best_i = 0, None
    for i, row in enumerate(rows[:scan]):
        s = _header_score(row)
        if s > best:
            best, best_i = s, i
    return best_i if best >= 4 else None


def importer_from_filename(name: str) -> str | None:
    stem = re.sub(r"[_\-.]+", " ", Path(name).stem.lower())
    for slug, full in sorted(KNOWN_IMPORTERS.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"(?<![a-z])" + re.escape(slug) + r"(?![a-z])", stem):
            return full
    return None


def date_from_filename(name: str) -> date | None:
    m = re.search(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})", name)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _cell(row: list[Any], idx: int | None) -> str | None:
    if idx is None or idx >= len(row):
        return None
    v = row[idx]
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    s = str(v).strip()
    return s or None


def _bag_kg(text: str | None, header: str) -> float | None:
    if not text:
        return None
    kg = parse_weight_kg(text)
    if kg:
        return kg
    try:
        v = float(re.sub(r"[^\d.]", "", text))
    except ValueError:
        return None
    if v <= 0:
        return None
    if "lb" in header:
        return v * KG_PER_LB
    if "kg" in header or v <= 75:
        return v
    return v * KG_PER_LB  # e.g. "132" or "152" without units are lb


@dataclass
class ParseResult:
    offers: list[OfferRecord] = field(default_factory=list)
    skipped: int = 0
    method: str = ""
    warnings: list[str] = field(default_factory=list)


def rows_to_offers(
    rows: list[list[Any]],
    *,
    importer: str | None,
    offer_date: date | None,
    source_name: str,
) -> ParseResult:
    res = ParseResult(method="table")
    hdr_i = find_header_row(rows)
    if hdr_i is None:
        res.warnings.append("no header row recognised")
        return res
    headers = rows[hdr_i]
    mapping = map_headers(headers)
    hnorm = [_norm_header(h) for h in headers]
    context_country: str | None = None

    for row in rows[hdr_i + 1:]:
        cells = [c for c in row if _cell([c], 0)]
        if not cells:
            continue
        # Repeated header (multi-page PDFs) -> skip.
        if _header_score(row) >= 4 and map_headers(row).keys() == mapping.keys():
            continue
        # Section row naming an origin, e.g. "ETHIOPIA" or "Colombia — Huila".
        if len(cells) == 1:
            iso = resolve_country(str(cells[0]), field=True)
            if iso:
                context_country = iso
            continue

        g = lambda f: _cell(row, mapping.get(f))  # noqa: E731
        lot = g("lot")
        region = g("region")
        country_txt = g("country")
        country = (
            resolve_country(country_txt, field=True) if country_txt else None
        ) or resolve_country(" ".join(x for x in (lot, region) if x)) or context_country

        bag_kg = _bag_kg(g("bag_size"), hnorm[mapping["bag_size"]] if "bag_size" in mapping else "")
        price_lb = None
        for f, basis in (("price_lb", "lb"), ("price_kg", "kg"), ("price_bag", "bag")):
            raw = g(f)
            if not raw:
                continue
            parsed = parse_price(raw)
            if not parsed:
                continue
            value, cell_basis = parsed
            b = cell_basis if cell_basis != "unknown" else basis
            if b == "lb" and value > 60 and bag_kg:
                b = "bag"  # a "price" column holding whole-bag prices
            price_lb = normalize_to_usd_lb(value, b, bag_kg)
            if price_lb:
                break
        if not price_lb or price_lb <= 0 or price_lb > 200:
            res.skipped += 1
            continue
        if not country:
            res.skipped += 1
            continue

        blob = " ".join(str(c) for c in cells)
        name = lot or " ".join(x for x in (country_txt, region) if x) or blob[:80]
        res.offers.append(
            OfferRecord(
                importer=g("importer") or importer or "Unknown importer",
                lot_name=name.strip(),
                spot_price_usd_lb=round(price_lb, 4),
                country_iso=country,
                offer_date=_parse_date(g("offer_date")) or offer_date,
                origin_region=region,
                process=normalize_process(g("process")) or detect_process(blob),
                variety=g("variety") or detect_variety(blob),
                sca_score=parse_score(g("score")) or detect_score(blob),
                bag_weight_kg=round(bag_kg, 3) if bag_kg else None,
                warehouse_location=g("warehouse"),
                bags_available=parse_int(g("bags")),
                raw_url=source_name,
            )
        )
    return res


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        d = pd.to_datetime(s, errors="coerce")
    except (ValueError, TypeError):
        return None
    if pd.isna(d):
        return None
    return d.date()


def _frame_rows(df: pd.DataFrame) -> list[list[Any]]:
    return df.astype(object).where(pd.notna(df), None).values.tolist()


def read_tabular(path_or_bytes: Path | bytes, filename: str) -> list[list[list[Any]]]:
    """Return a list of grids (one per sheet) for CSV/TSV/XLSX."""
    suffix = Path(filename).suffix.lower()
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes
    if suffix in (".xlsx", ".xlsm", ".xls"):
        sheets = pd.read_excel(src, sheet_name=None, header=None, dtype=str)
        return [_frame_rows(df) for df in sheets.values()]
    sep = "\t" if suffix == ".tsv" else None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            if isinstance(src, io.BytesIO):
                src.seek(0)
            df = pd.read_csv(src, header=None, dtype=str, sep=sep, engine="python", encoding=enc)
            return [_frame_rows(df)]
        except UnicodeDecodeError:
            continue
    raise ValueError("could not decode CSV")


def read_pdf_tables(path_or_bytes: Path | bytes) -> list[list[list[Any]]]:
    import pdfplumber

    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes
    grids: list[list[list[Any]]] = []
    merged: list[list[Any]] = []
    with pdfplumber.open(src) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            if not tables:
                # Text-only pages: split lines on 2+ spaces as a crude table.
                text = page.extract_text() or ""
                lines = [re.split(r"\s{2,}", ln.strip()) for ln in text.splitlines() if ln.strip()]
                if lines:
                    tables = [lines]
            for t in tables:
                merged.extend(t)
    if merged:
        grids.append(merged)
    return grids


def parse_spot_sheet(
    data: bytes,
    filename: str,
    *,
    importer: str | None = None,
    offer_date: date | None = None,
    allow_llm: bool = True,
) -> ParseResult:
    importer = importer or importer_from_filename(filename)
    offer_date = offer_date or date_from_filename(filename) or date.today()
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        grids = read_pdf_tables(data)
    elif suffix in (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls"):
        grids = read_tabular(data, filename)
    else:
        raise ValueError(f"unsupported spot sheet type: {suffix}")

    best = ParseResult(method="table")
    for grid in grids:
        r = rows_to_offers(grid, importer=importer, offer_date=offer_date, source_name=filename)
        best.offers.extend(r.offers)
        best.skipped += r.skipped
        best.warnings.extend(r.warnings)

    if suffix == ".pdf" and not best.offers and allow_llm:
        from . import llm_extract

        if llm_extract.available():
            offers = llm_extract.extract_offers_from_pdf(
                data, filename=filename, importer=importer, offer_date=offer_date
            )
            return ParseResult(offers=offers, method="llm", warnings=best.warnings)
        best.warnings.append("no tables parsed; set ANTHROPIC_API_KEY to enable LLM extraction")
    return best


def iter_sheet_files(folder: Path) -> Iterable[Path]:
    exts = {".csv", ".tsv", ".xlsx", ".xlsm", ".xls", ".pdf"}
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in exts and not p.name.startswith((".", "~$")):
            yield p


def file_mtime_date(p: Path) -> date:
    return datetime.fromtimestamp(p.stat().st_mtime).date()


# Bag sizes that are clearly whole export bags (used by the UI to label offers).
def is_export_bag(kg: float | None) -> bool:
    return bool(kg) and any(abs(kg - b) < 0.6 for b in STANDARD_BAG_KG)
