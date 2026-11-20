# OriginPulse — Green Coffee Price Intelligence & Atlanta Landed-Cost Tracker

Tracks green coffee prices across ~72 producing countries for a specialty roaster in Atlanta, GA:
global benchmarks, live importer spot offers, customs trade valuations, landed cost into Atlanta,
and buying-opportunity alerts.

![stack](https://img.shields.io/badge/stack-FastAPI%20%2B%20SQLite%20%2B%20Next.js-blue)

## Quick start

**Windows (PowerShell)** — no make/docker needed:

```powershell
.\dev.ps1
```

**macOS / Linux:** `make dev` · **Docker:** `docker compose up --build`

Then open http://localhost:3000 (dashboard) and http://127.0.0.1:8000/docs (API).
On first start the scheduler backfills 5 years of ICE "C"/FX and ~18 months of customs data in the background.

## Streamlit version (easiest to share)

`streamlit_app.py` is a single-process version of the dashboard: the same pipelines, cost engine and alerts, with no API
server or Node build.

```powershell
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\streamlit run streamlit_app.py
```

### Deploy to Streamlit Community Cloud (free)

1. Push this repo to GitHub (the branch you want to deploy).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **Create app** → **Deploy a public app from GitHub**.
3. Pick the repo and branch, set **Main file path** to `streamlit_app.py`, and under **Advanced settings** choose Python 3.12.
4. Optional: paste secrets under **Advanced settings → Secrets** (template: `.streamlit/secrets.toml.example`).
   Set `ADMIN_PASSWORD` so visitors can't run pipelines, upload sheets or edit freight rules.
5. **Deploy.** The first load takes about 30 seconds while 5 years of ICE "C"/FX prices download. Importer offers follow
   within a minute, and customs data fills in over roughly 10 minutes (UN Comtrade is rate-limited), all in the background.

How it behaves on Community Cloud:

- **No scheduler.** Data refreshes when someone visits and it is stale: prices and offers after 12 hours, customs data after 7 days.
  Alerts run after each refresh.
- **Temporary disk.** The SQLite database is rebuilt whenever the app restarts or wakes from sleep, so price *history* for
  importer offers starts over. To keep it, set `DATABASE_URL` to a hosted PostgreSQL database (Neon and Supabase have free tiers)
  and add `psycopg[binary]` to `requirements.txt`.
- **Spot sheets** uploaded on the Data Sources page are ingested immediately; the files themselves aren't kept after a restart.
- Playwright portal logins and the email webhook are not available in the Streamlit version.

## What's in it

| Page | What it shows |
|---|---|
| **Overview** | ICE "C" settle, 30-day MA, 52-week range; world choropleth (price / differential / 30d change); latest alerts; origin FX |
| **Origin Matrix** | All producing countries grouped by region: spot avg, differential vs "C", customs unit value, 7d/30d/YoY change, harvest status + calendar |
| **Spot Offers** | Current importer lots with freight → landed Atlanta → true roasted cost; Southeast-warehouse filter; shrinkage slider |
| **Trends** | Spot vs "C" for any country + process over 30d/90d/1y/3y, differential spread (widening/compressing), customs unit values; table view |
| **Landed Cost** | Simulator ($/lb, $/kg or per bag; 60/69/70 kg bags) with per-warehouse comparison; editable freight ruleset |
| **Alerts** | Origin drops, value-per-point deals, Southeast arrivals, FX triggers |
| **Data Sources** | Run any pipeline, drag-and-drop spot sheets, run log |

`$/lb ↔ $/kg` toggles globally in the header.

## Data tiers

**Tier 1 — benchmarks & FX** (`backend/app/pipelines/benchmarks.py`, weekdays 6:15pm ET)
- ICE Arabica "C" (`KC=F`, US¢/lb → $/lb) and FX for BRL, COP, ETB, HNL, GTQ, KES, VND, IDR, PEN, MXN, UGX, INR, NIO, CRC, RWF, TZS from Yahoo Finance's chart API (no key). Today's still-forming bar is skipped.
- **ICE Robusta isn't on Yahoo.** Drop daily settlements into `data/ice_robusta.csv` (`date,usd_tonne`) or set `ROBUSTA_YAHOO_SYMBOL`.
- **ICO indicator prices** have no API: drop `data/ico_prices.csv` (`date,composite,colombian_milds,other_milds,brazilian_naturals,robustas`, US¢/lb).

**Tier 2 — importer spot offers** (`scrapers/`, daily 7am; dropzone every 5 min)
- Public catalogs (Shopify JSON): Royal Coffee Crown Jewels, Bodhi Leaf. SuiteCommerce: Genuine Origin (best-effort — its search API returns few items).
  Coffee Shrub is registered but disabled: its store doesn't serve the JSON catalog.
- **Spot-sheet dropzone:** save CSV/XLSX/PDF sheets into `data/spot_sheets/` (or upload on Data Sources). The parser finds the header row, handles
  country section rows, $/lb, $/kg or per-bag prices and kg/lb bag sizes. Name files like `atlas_2026-11-14.pdf` to tag importer + date.
  PDFs whose tables can't be parsed fall back to Claude extraction when `ANTHROPIC_API_KEY` is set (`pip install -r backend/requirements-optional.txt`).
- **Email forwarding:** set `INGEST_WEBHOOK_TOKEN` and point an inbound-parse service (Mailgun/SendGrid/Postmark) at `POST /api/ingest/email?token=…`.
- **Authenticated portals** (Cafe Imports, Atlas, Ally, ofi, Sucafina): copy `data/portals.example.json` → `data/portals.json`, fill selectors,
  put credentials in `.env`, install Playwright. Downloads land in the dropzone.

**Tier 3 — customs unit values** (`backend/app/pipelines/trade.py`, weekly)
- HS 0901.11 import value ÷ net weight from UN Comtrade for the US, Germany and Japan (monthly + annual), giving a price for every origin that ships.
- Comtrade's recent **US monthly weights are estimated** from value (flat ~$3.00/lb for every origin), so those rows are dropped. Set a free
  `CENSUS_API_KEY` to get real US monthly data from the Census trade API.
- The matrix uses one primary market per origin (largest volume) over a rolling 3-month window so tiny shipments don't swing the number.

## Cost model

```
Landed green $/lb  = spot $/lb + LTL freight to Atlanta $/lb + warehouse/financing fee $/lb
True roasted $/lb  = landed $/lb ÷ (1 − shrinkage)
```

Freight tiers (editable on the Landed Cost page, stored in `data/freight_rules.json`):
Southeast (Savannah, Charleston, Jacksonville) $0.12–0.18 · Northeast/Miami/New Orleans $0.22–0.30 ·
West Coast/Houston $0.38–0.55 · unknown warehouse → $0.45. Default fee $0.05/lb, default shrinkage 15%.

Origin spot averages use full-bag lots (≥20 kg) when available; small boxes (e.g. 22 lb Crown Jewels) carry a small-lot premium and
are only used — and labelled “small-pack” — when nothing else is offered.

## Alerts

Daily 7:45am ET, deduplicated for 7 days, delivered to the dashboard, the terminal (`python -m backend.app.cli digest`), Slack (`SLACK_WEBHOOK_URL`) and email (`SMTP_*`).

- **Origin price drop:** origin spot average down >5% over 14 days (same pack basis).
- **Value per point:** 87+ SCA lots priced under the 30-day average for that origin + process.
- **Southeast arrival:** a lot newly listed in a Savannah/Charleston/Jacksonville warehouse (not on an importer's first scrape).
- **FX trigger:** a producer currency weakens >3% vs USD in a week (3-day medians, so one bad print can't fire it).

Thresholds are in `.env.example`.

## CLI

```
python -m backend.app.cli init | benchmarks | trade [--months N] | scrape [--only k] [--dry-run]
                          portals | dropzone | ingest FILE | alerts [--no-deliver] | digest | all
```

## Layout

```
backend/app/      FastAPI app, models, cost engine, analytics, alerts, scheduler, pipelines/
scrapers/         importer scrapers, spot-sheet parser, Playwright portal, LLM fallback
frontend/         Next.js 16 + Tailwind + Recharts dashboard
data/             countries_seed.json, freight_rules.json, spot_sheets/ (dropzone), SQLite DB
tests/            pytest suite (units, freight, parsers, alerts, API)
```

## Notes & limits

- Harvest windows and "typical differential" ranges in `data/countries_seed.json` are indicative seed values — edit them to match your sourcing.
- Customs unit values are a broad commodity signal across all grades, not a specialty price.
- Public-catalog prices are small-lot retail/box prices; your real buying prices come from importer spot sheets (dropzone / portals / email).
- SQLite by default; for PostgreSQL `pip install "psycopg[binary]"` and set `DATABASE_URL=postgresql+psycopg://…`.
- `docker-compose.yml` and the Dockerfiles are provided but weren't exercised on this machine (no Docker installed).
