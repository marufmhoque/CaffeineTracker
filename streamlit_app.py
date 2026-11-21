"""OriginPulse on Streamlit — green coffee price intelligence for an Atlanta specialty roaster.

Run locally:   streamlit run streamlit_app.py
Deploy:        Streamlit Community Cloud → New app → main file `streamlit_app.py` (see README).

This reuses the FastAPI backend's pipelines, analytics and cost engine directly (no API server).
On a fresh container the database is empty, so the app backfills ICE "C"/FX synchronously and
loads importer offers, market signals and customs data in a background thread, then refreshes stale
data on visits.
"""

from __future__ import annotations

import html
import os
import threading
from datetime import datetime, timedelta, timezone

import streamlit as st


# ---------------------------------------------------------------- secrets → env (before backend imports)

def _secrets_to_env() -> None:
    """Expose Streamlit secrets (API keys, thresholds) as env vars, which backend.app.config reads."""
    try:
        items = dict(st.secrets)
    except Exception:  # noqa: BLE001 — no secrets.toml locally is fine
        return
    for k, v in items.items():
        if isinstance(v, (str, int, float, bool)):
            os.environ.setdefault(k, str(v))


_secrets_to_env()
os.environ.setdefault("ENABLE_SCHEDULER", "0")  # Streamlit has no long-lived worker; refresh on visit instead

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlmodel import select  # noqa: E402

from backend.app import analytics, config, costing, jobs, signals  # noqa: E402
from backend.app.alerts import ALERT_TYPES, TITLES, build_digest  # noqa: E402
from backend.app.db import init_db, last_successful_run, session_scope  # noqa: E402
from backend.app.models import (  # noqa: E402
    Alert,
    CropEstimate,
    FuturesQuote,
    IngestedFile,
    IngestRun,
    MarketBenchmark,
    SpotOffer,
    TradeValuation,
    WeatherSignal,
)
from backend.app.pipelines import market, spot  # noqa: E402
from backend.app.units import LB_PER_KG, STANDARD_BAG_KG  # noqa: E402
from scrapers import SCRAPERS  # noqa: E402

st.set_page_config(page_title="OriginPulse", page_icon="☕", layout="wide")

# Chart palette (validated for colour-vision deficiency): amber = Arabica / ICE "C", blue = spot, green, violet = Robusta.
SERIES = ["#d97706", "#2563eb", "#059669", "#7c3aed"]
SEQ_LIGHT = ["#fef3c7", "#fde68a", "#fcd34d", "#f59e0b", "#d97706", "#b45309", "#78350f"]
SEQ_DARK = ["#451a03", "#78350f", "#92400e", "#b45309", "#d97706", "#f59e0b", "#fcd34d"]
DIVERGING = ["#1d4ed8", "#93c5fd", "#f4f4f5", "#fda4af", "#be123c"]  # cheaper (blue) ← 0 → dearer (rose)
REGIONS = ["Central America & Mexico", "South America", "Caribbean", "East & Central Africa", "West Africa", "Asia & Oceania"]
MONTHS = "JFMAMJJASOND"
RANGE_LABELS = {"1m": "1M", "6m": "6M", "1y": "1Y", "3y": "3Y", "5y": "5Y", "max": "MAX"}
REFRESH_EVERY = {
    "benchmarks": timedelta(hours=12), "market": timedelta(hours=12), "scrapers": timedelta(hours=12),
    "weather": timedelta(hours=24), "usda": timedelta(days=7), "trade": timedelta(days=7),
}

CSS = """
<style>
.op-card{border:1px solid var(--op-border);border-radius:12px;padding:16px 18px;background:var(--op-surface);height:100%}
.op-eyebrow{font-size:11px;font-weight:500;letter-spacing:.06em;text-transform:uppercase;color:var(--op-muted)}
.op-big{font-family:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums;
  font-size:clamp(16px,1.7vw,26px);font-weight:600;letter-spacing:-.02em;line-height:1.15;margin-top:10px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.op-sub{font-family:'JetBrains Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums;font-size:13px;color:var(--op-muted);margin-top:2px}
.op-row{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:12px;color:var(--op-muted)}
.op-pill{display:inline-flex;align-items:center;gap:3px;border-radius:6px;padding:1px 6px;font-size:12px;font-weight:500;
  font-family:'JetBrains Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}
.op-pos{background:var(--op-pos-bg);color:var(--op-pos)} .op-neg{background:var(--op-neg-bg);color:var(--op-neg)}
.op-flat{background:var(--op-flat-bg);color:var(--op-muted)} .op-warn{background:var(--op-warn-bg);color:var(--op-warn)}
.op-acc{background:var(--op-warn-bg);color:var(--op-warn)}
.op-txt{font-family:inherit;font-size:11.5px}
.op-mono{font-family:'JetBrains Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}
[data-testid="stMetricValue"]{font-family:'JetBrains Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}
</style>
"""
VARS_LIGHT = ("--op-border:rgba(228,228,231,.9);--op-surface:#ffffff;--op-muted:#71717a;--op-pos:#059669;--op-pos-bg:#ecfdf5;"
              "--op-neg:#e11d48;--op-neg-bg:#fff1f2;--op-flat-bg:#f4f4f5;--op-warn:#b45309;--op-warn-bg:#fffbeb;")
VARS_DARK = ("--op-border:rgba(63,63,70,.7);--op-surface:#131316;--op-muted:#8a8a93;--op-pos:#34d399;--op-pos-bg:rgba(16,185,129,.12);"
             "--op-neg:#fb7185;--op-neg-bg:rgba(244,63,94,.12);--op-flat-bg:#27272a;--op-warn:#fbbf24;--op-warn-bg:rgba(245,158,11,.12);")


# ---------------------------------------------------------------- bootstrap & refresh

def _bg(fn, name: str) -> None:
    threading.Thread(target=fn, daemon=True, name=f"op-{name}").start()


def _background_load(need: dict[str, bool]) -> None:
    if need.get("market"):
        jobs.job_market()
    if need.get("scrapers"):
        jobs.job_scrapers()
    jobs.job_dropzone()
    jobs.job_alerts(deliver=False)
    if need.get("weather"):
        jobs.job_weather()
    if need.get("usda"):
        jobs.job_usda()
    if need.get("trade"):
        jobs.job_trade()  # slowest (Comtrade rate limits): several minutes on an empty database
        jobs.job_alerts(deliver=False)


@st.cache_resource(show_spinner=False)
def bootstrap() -> dict:
    """Runs once per server process: schema, seed data, and first data load."""
    init_db()
    costing.load_rules()
    config.SPOT_SHEETS_DIR.mkdir(parents=True, exist_ok=True)
    with session_scope() as s:
        empty = lambda model: s.exec(select(model.id).limit(1)).first() is None  # noqa: E731
        need = {
            "benchmarks": empty(MarketBenchmark), "scrapers": empty(SpotOffer), "trade": empty(TradeValuation),
            "market": empty(FuturesQuote), "weather": empty(WeatherSignal), "usda": empty(CropEstimate),
        }
    if need["benchmarks"]:
        jobs.job_benchmarks()  # ICE "C" back to 2000 + 5 years of FX; the dashboard needs it to be useful
    if any(v for k, v in need.items() if k != "benchmarks"):
        _bg(lambda: _background_load(need), "bootstrap")
    return {"started": datetime.now(timezone.utc), "cold_start": need["benchmarks"]}


def refresh_if_stale() -> None:
    """Kick off background refreshes for data older than REFRESH_EVERY (jobs are locked, so no duplicates)."""
    now = datetime.now(timezone.utc)
    last = st.session_state.get("_refresh_check")
    if last and now - last < timedelta(minutes=30):
        return
    st.session_state["_refresh_check"] = now
    with session_scope() as s:
        stale = []
        for job, age in REFRESH_EVERY.items():
            ts = last_successful_run(s, job)
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts and now - ts > age:
                stale.append(job)
    if stale:
        def run():
            for j in stale:
                jobs.JOBS[j]()
            jobs.job_alerts(deliver=False)
        _bg(run, "refresh")


def running_jobs() -> list[str]:
    return [name for name, lock in jobs._locks.items() if lock.locked()]


def data_version() -> tuple:
    """Changes whenever a job finishes or a file is ingested, so cached queries refresh automatically."""
    with session_scope() as s:
        runs = s.exec(select(func.count(IngestRun.id), func.max(IngestRun.finished_at))).one()
        alerts = s.exec(select(func.count(Alert.id), func.sum(Alert.is_read))).one()
    return (*runs, *alerts, costing.load_rules().__repr__())


# ---------------------------------------------------------------- cached reads

@st.cache_data(ttl=600, show_spinner=False)
def q_matrix(v, species: str = "All") -> dict:
    with session_scope() as s:
        return analytics.country_matrix(s, species=species)


@st.cache_data(ttl=600, show_spinner=False)
def q_summary(v) -> dict:
    with session_scope() as s:
        return analytics.benchmark_summary(s)


@st.cache_data(ttl=600, show_spinner=False)
def q_bench(v, range_: str) -> dict:
    with session_scope() as s:
        return signals.benchmark_history(s, range_)


@st.cache_data(ttl=600, show_spinner=False)
def q_signals(v) -> dict:
    with session_scope() as s:
        return signals.signals(s)


@st.cache_data(ttl=600, show_spinner=False)
def q_detail(v, iso: str, shrink: float) -> dict | None:
    with session_scope() as s:
        return signals.country_detail(s, iso, shrink)


@st.cache_data(ttl=600, show_spinner=False)
def q_offers(v, species: str = "All") -> pd.DataFrame:
    with session_scope() as s:
        names = {c.iso_code: c.name for c in s.exec(select(analytics.Country)).all()}
        rows = analytics.current_offers(s, species=None if species == "All" else species)
        out = [{
            "Importer": o.importer, "Country": names.get(o.country_iso or "", o.country_iso or "—"),
            "iso": o.country_iso, "Lot": o.lot_name, "Producer": o.origin_region, "Species": o.species,
            "Process": o.process, "Variety": o.variety,
            "Score": o.sca_score, "Bag kg": o.bag_weight_kg, "Warehouse": o.warehouse_location,
            "Tier": o.warehouse_tier, "Southeast": o.is_southeast, "spot": o.spot_price_usd_lb,
            "freight": o.freight_usd_lb, "landed": o.atlanta_landed_usd_lb, "Bags avail.": o.bags_available,
            "Link": o.raw_url if (o.raw_url or "").startswith("http") else None,
            "Scraped": o.scraped_at,
        } for o in rows]
    df = pd.DataFrame(out)
    if not df.empty:
        num = ["Score", "Bag kg", "spot", "freight", "landed", "Bags avail."]
        df[num] = df[num].apply(pd.to_numeric, errors="coerce")
        txt = ["Producer", "Species", "Process", "Variety", "Warehouse"]
        df[txt] = df[txt].fillna("")
    return df


@st.cache_data(ttl=600, show_spinner=False)
def q_history(v, iso: str, process: str | None, range_: str) -> dict:
    with session_scope() as s:
        return analytics.history(s, iso, process, range_)


@st.cache_data(ttl=600, show_spinner=False)
def q_alerts(v) -> list[dict]:
    with session_scope() as s:
        rows = s.exec(select(Alert).order_by(Alert.created_at.desc()).limit(300)).all()
        return [a.model_dump() for a in rows]


@st.cache_data(ttl=600, show_spinner=False)
def q_runs(v) -> tuple[list[dict], list[dict]]:
    with session_scope() as s:
        runs = s.exec(select(IngestRun).order_by(IngestRun.started_at.desc()).limit(60)).all()
        files = s.exec(select(IngestedFile).order_by(IngestedFile.ingested_at.desc()).limit(60)).all()
        return [r.model_dump() for r in runs], [f.model_dump() for f in files]


# ---------------------------------------------------------------- units & formatting

def unit() -> str:
    return st.session_state.get("unit", "$/lb")


def alt_unit() -> str:
    return "$/kg" if unit() == "$/lb" else "$/lb"


def k(u: str | None = None) -> float:
    """Multiplier from $/lb to a display unit (default: the selected primary unit)."""
    return LB_PER_KG if (u or unit()) == "$/kg" else 1.0


def _fmt(v: float, u: str, digits: int, sign: bool) -> str:
    x = v * k(u)
    s = f"{'+' if x >= 0 else '−'}${abs(x):,.{digits}f}" if sign else f"${x:,.{digits}f}"
    return f"{s}/{u[2:]}"


def dual(v: float | None, digits: int = 2, sign: bool = False) -> tuple[str, str]:
    """(primary, secondary) strings, e.g. ('$2.82/lb', '$6.22/kg')."""
    if v is None or v != v:
        return "—", ""
    return _fmt(v, unit(), digits, sign), _fmt(v, alt_unit(), digits, sign)


def dual_text(v: float | None, digits: int = 2, sign: bool = False) -> str:
    p, s = dual(v, digits, sign)
    return p if not s else f"{p} ({s})"


def money(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"${v * k():,.{digits}f}"


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def is_dark() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:  # noqa: BLE001
        return False


def seq_scale() -> list[str]:
    return SEQ_DARK if is_dark() else SEQ_LIGHT


def money_cols(df: pd.DataFrame, cols: dict[str, str], digits: int = 2) -> dict:
    """Add primary + secondary unit columns for each raw $/lb column; return their column_config."""
    cfg = {}
    for raw, label in cols.items():
        for u in (unit(), alt_unit()):
            name = f"{label} {u}"
            df[name] = df[raw] * k(u)
            cfg[name] = st.column_config.NumberColumn(name, format=f"$%.{digits}f")
    return cfg


def dual_names(labels: list[str]) -> list[str]:
    return [f"{lbl} {u}" for lbl in labels for u in (unit(), alt_unit())]


def is_admin() -> bool:
    pw = os.getenv("ADMIN_PASSWORD", "")
    return not pw or st.session_state.get("admin_ok", False)


def admin_gate(what: str) -> bool:
    if is_admin():
        return True
    st.info(f"{what} is limited to admins on this deployment. Enter the admin password in the sidebar.", icon="🔒")
    return False


def harvest_strip(main: list[int], fly: list[int]) -> str:
    return "".join(m if (i + 1) in main else (m.lower() if (i + 1) in fly else "·") for i, m in enumerate(MONTHS))


def chart_layout(fig: go.Figure, height: int = 320, yfmt: str = "$.2f") -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8), hovermode="x unified",
                      legend=dict(orientation="h", y=1.1, x=0, title=None), yaxis_tickformat=yfmt,
                      font=dict(family="Inter, system-ui, sans-serif"),
                      hoverlabel=dict(font_family="JetBrains Mono, ui-monospace, monospace"))
    fig.update_xaxes(showgrid=False, title=None)
    fig.update_yaxes(title=None, gridcolor="rgba(128,128,128,0.15)")
    return fig


# ---------------------------------------------------------------- HTML bits

def pill(v: float | None, label: str = "") -> str:
    if v is None:
        return f'<span class="op-pill op-flat">—</span>{(" " + html.escape(label)) if label else ""}'
    cls, arrow = ("op-flat", "■") if abs(v) < 0.05 else (("op-pos", "▲") if v > 0 else ("op-neg", "▼"))
    return f'<span class="op-pill {cls}">{arrow} {v:+.1f}%</span>{(" " + html.escape(label)) if label else ""}'


def badge(text: str, tone: str = "flat") -> str:
    return f'<span class="op-pill op-txt op-{tone}">{html.escape(text)}</span>'


RISK_TONE = {"Low": ("pos", "●"), "Moderate": ("warn", "▲"), "Critical": ("neg", "◆")}


def risk_badge(risk: str | None, prefix: str = "Weather risk") -> str:
    if not risk:
        return badge(f"{prefix}: no data")
    tone, icon = RISK_TONE.get(risk, RISK_TONE["Low"])
    return badge(f"{icon} {prefix + ': ' if prefix else ''}{risk}", tone)


def kpi(col, label: str, usd_lb: float | None = None, *, digits: int = 2, value: str | None = None,
        sub: str | None = None, foot: str = "") -> None:
    if value is None:
        value, sub2 = dual(usd_lb, digits)
        sub = sub if sub is not None else sub2
    col.markdown(
        f'<div class="op-card"><div class="op-eyebrow">{html.escape(label)}</div>'
        f'<div class="op-big">{html.escape(value)}</div><div class="op-sub">{html.escape(sub or "")}</div>'
        f'<div class="op-row">{foot}</div></div>',
        unsafe_allow_html=True,
    )


def segmented(label: str, options: dict[str, str], default: str, key: str) -> str:
    val = st.segmented_control(label, list(options), default=default, key=key, format_func=options.get,
                               label_visibility="collapsed")
    return val or default


def species_toggle(key: str, default: str = "Arabica") -> str:
    return segmented("Species", {"Arabica": "Arabica", "Robusta": "Robusta", "All": "All"}, default, key)


# ---------------------------------------------------------------- map & origin drawer

def world_map(rows: list[dict], metric: str, key: str, height: int = 430) -> None:
    """Choropleth with a clean tooltip (country · dual price); clicking a country opens its drawer."""
    df = pd.DataFrame(rows)
    df["price"] = pd.to_numeric(df["spot_avg_usd_lb"], errors="coerce").fillna(
        pd.to_numeric(df["trade_unit_usd_lb"], errors="coerce"))  # customs value where no live lots
    if metric == "price":
        col, title = "price", f"Price {unit()}"
    elif metric == "diff":
        col, title = "diff_vs_c_usd_lb", f"Differential {unit()}"
    else:
        col, title = "chg_30d_pct", "30-day change %"
    df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df[col].notna()].copy()
    if df.empty:
        st.caption("No data for this metric yet.")
        return
    df["color"] = df[col] * (1 if metric == "chg30" else k())
    df["tip"] = df["price"].map(lambda v: dual_text(v))
    if metric == "diff":
        df["extra"] = df["diff_vs_c_usd_lb"].map(lambda v: f"<br>{dual(v, 2, True)[0]} vs benchmark")
    elif metric == "chg30":
        df["extra"] = df["chg_30d_pct"].map(lambda v: f"<br>{v:+.1f}% in 30 days")
    else:
        df["extra"] = ""
    # Clip the colour range to the 5th–95th percentile so one boutique lot doesn't wash out every other country.
    lo, hi = df["color"].quantile(0.05), df["color"].quantile(0.95)
    if metric == "chg30":
        m = max(abs(lo), abs(hi), 1)
        scale, rng = DIVERGING, (-m, m)
    else:
        scale, rng = seq_scale(), (lo, hi if hi > lo else lo + 1)
    fig = go.Figure(go.Choropleth(
        locations=df["iso_code"], z=df["color"], customdata=df[["name", "tip", "extra", "iso_code"]].values,
        colorscale=scale, zmin=rng[0], zmax=rng[1], marker_line_color="rgba(255,255,255,0.6)", marker_line_width=0.4,
        hovertemplate="<b>%{customdata[0]}</b> · %{customdata[1]}%{customdata[2]}<extra></extra>",
        colorbar=dict(title=title, thickness=10, len=0.6, tickformat="$.2f" if metric != "chg30" else "+.0f"),
    ))
    fig.update_geos(showframe=False, showcoastlines=False, projection_type="natural earth",
                    showcountries=True, countrycolor="rgba(128,128,128,0.35)",
                    showland=True, landcolor="rgba(128,128,128,0.10)", bgcolor="rgba(0,0,0,0)",
                    lataxis_range=[-45, 50])
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=0, b=0), dragmode=False,
                      hoverlabel=dict(bgcolor="white" if not is_dark() else "#131316", bordercolor="rgba(128,128,128,.3)",
                                      font=dict(family="Inter, system-ui, sans-serif", size=13,
                                                color="#18181b" if not is_dark() else "#fafafa")))
    event = st.plotly_chart(fig, width="stretch", key=key, on_select="rerun", selection_mode="points",
                            config={"displayModeBar": False})
    pts = (event or {}).get("selection", {}).get("points", []) if isinstance(event, dict) else \
        getattr(getattr(event, "selection", None), "points", [])
    if pts:
        p = pts[0]
        iso = p.get("location") or (p.get("customdata") or [None] * 4)[3]
        if iso and st.session_state.get(f"{key}_last") != iso:
            st.session_state[f"{key}_last"] = iso
            origin_drawer(iso)
    else:
        st.session_state.pop(f"{key}_last", None)


def _dialog(title: str):
    try:
        return st.dialog(title, width=640, position="right")
    except TypeError:  # Streamlit < 1.67 has no side-docked dialog
        return st.dialog(title, width="large")


@_dialog("Origin details")
def origin_drawer(iso: str) -> None:
    v = data_version()
    rules = costing.load_rules()
    d = q_detail(v, iso, float(rules["default_shrinkage"]))
    if not d:
        st.error("Unknown country")
        return
    st.markdown(f'<div class="op-eyebrow">{html.escape(d["region"])}</div>', unsafe_allow_html=True)
    st.subheader(d["name"], anchor=False)
    badges = [badge(d["harvest_status"], "acc"), risk_badge(d["weather_risk"])]
    if d["crop"] and d["crop"]["cycle"]:
        badges.append(badge(f"{d['crop']['cycle']} {d['crop']['market_year']}", "pos" if d["crop"]["cycle"] == "On-year" else "warn"))
    st.markdown(" ".join(badges), unsafe_allow_html=True)

    p = d["prices"] or {}
    head = p.get("spot_avg_usd_lb") if p.get("spot_avg_usd_lb") is not None else p.get("trade_unit_usd_lb")
    a, b = st.columns(2)
    kpi(a, "Spot average" if p.get("spot_avg_usd_lb") is not None else "Customs unit value", head,
        foot=pill(p.get("chg_30d_pct"), "30d"))
    dv, ds = dual(p.get("diff_vs_c_usd_lb"), 2, True)
    lo, hi = d["typical_diff_usd_lb"]
    typical = f"Typical {dual(lo, 2, True)[0]} to {dual(hi, 2, True)[0]}" if lo is not None else ""
    kpi(b, "Differential vs benchmark", value=dv, sub=ds, foot=html.escape(typical))

    st.markdown("**Harvest window**")
    now = datetime.now().month
    cells = "".join(
        f'<div style="text-align:center;flex:1"><div style="height:8px;border-radius:3px;background:'
        f'{"#d97706" if (i + 1) in d["main_harvest_months"] else ("#fde68a" if (i + 1) in d["fly_harvest_months"] else "rgba(128,128,128,.15)")};'
        f'{"outline:2px solid currentColor;outline-offset:1px;" if i + 1 == now else ""}"></div>'
        f'<div style="font-size:10px;margin-top:3px;opacity:{1 if i + 1 == now else .6}">{m}</div></div>'
        for i, m in enumerate(MONTHS)
    )
    st.markdown(f'<div style="display:flex;gap:4px">{cells}</div>', unsafe_allow_html=True)

    share = d["species_share"]
    if share:
        st.markdown(f"**Species** · <span style='opacity:.6;font-size:12px'>{html.escape(d['species_share_basis'])}</span>",
                    unsafe_allow_html=True)
        colors = {"Arabica": "#d97706", "Robusta": "#7c3aed", "Liberica": "#059669"}
        bar = "".join(f'<div style="width:{v}%;background:{colors.get(s, "#a1a1aa")}"></div>' for s, v in share.items())
        legend = " · ".join(f"{s} {v}%" for s, v in share.items())
        st.markdown(f'<div style="display:flex;height:8px;border-radius:99px;overflow:hidden;gap:2px">{bar}</div>'
                    f'<div class="op-sub" style="font-size:12px;margin-top:4px">{legend}</div>', unsafe_allow_html=True)
    if d["crop"]:
        c = d["crop"]
        st.markdown(
            f'<div class="op-row">USDA {c["market_year"]}: <b class="op-mono">{c["production_k_bags"] / 1000:.1f}M bags</b>'
            f' {pill(c["yoy_pct"], "vs last year")} {pill(c["vs_5y_avg_pct"], "vs 5-yr avg")}</div>', unsafe_allow_html=True)

    if d["weather"]:
        st.markdown("**Growing belts**")
        for w in d["weather"]:
            rain = f"{w['rain_pct_normal']:.0f}%" if w["rain_pct_normal"] is not None else "—"
            why = "" if w["risk"] == "Low" else " · " + " · ".join(w["reasons"])
            st.markdown(f"{risk_badge(w['risk'], '')} **{html.escape(w['belt'])}** · {w['species']}  \n"
                        f"<span class='op-sub'>Rain {rain} of normal (90d) · {w['hot_days_30d']} hot days (30d){html.escape(why)}</span>",
                        unsafe_allow_html=True)

    lots = d["lots"]
    st.markdown(f"**Available lots** · <span style='opacity:.6;font-size:12px'>{len(lots)} lots · landed Atlanta · "
                f"roasted at {d['shrinkage'] * 100:.0f}% shrink</span>", unsafe_allow_html=True)
    if not lots:
        st.caption("No current offers for this origin. Upload a spot sheet on Data Sources to add lots.")
    for lot in sorted(lots, key=lambda x: x["landed_usd_lb"] or 1e9):
        with st.container(border=True):
            l, r = st.columns([3, 2])
            title = f"[{lot['lot_name']}]({lot['raw_url']})" if lot["raw_url"] else lot["lot_name"]
            l.markdown(f"**{title}**  \n<span class='op-sub'>{html.escape(' · '.join(x for x in [lot['producer'], lot['importer']] if x))}</span>",
                       unsafe_allow_html=True)
            tags = [x for x in [lot["species"], lot["process"], lot["variety"],
                                f"{lot['sca_score']:.1f} pts" if lot["sca_score"] else None,
                                f"{lot['bag_weight_kg']:.0f} kg bag" if lot["bag_weight_kg"] else None,
                                lot["warehouse"]] if x]
            l.markdown(" ".join(badge(t, "pos" if t == lot["warehouse"] and lot["is_southeast"] else "flat") for t in tags),
                       unsafe_allow_html=True)
            pv, sv = dual(lot["landed_usd_lb"])
            r.markdown(f'<div style="text-align:right"><div class="op-eyebrow">Landed ATL</div>'
                       f'<div class="op-mono" style="font-weight:600;font-size:16px">{pv}</div><div class="op-sub">{sv}</div>'
                       f'<div class="op-sub" style="font-size:11px">spot {money(lot["spot_usd_lb"])} · roasted {money(lot["roasted_usd_lb"])}</div></div>',
                       unsafe_allow_html=True)
    if st.button("Open 5-year trend", key=f"trend_{iso}"):
        st.session_state["trend_country"] = iso
        st.switch_page(TRENDS_PAGE)


# ---------------------------------------------------------------- pages

def page_overview() -> None:
    v = data_version()
    summ = q_summary(v)
    sig = q_signals(v)
    ice, rob = summ["ice_arabica"], summ["ice_robusta"]

    head, tog = st.columns([3, 1.2], vertical_alignment="bottom")
    with head:
        st.title("Market overview", anchor=False)
    with tog:
        species = species_toggle("ov_species")
    mat = q_matrix(v, species)
    priced = sum(1 for c in mat["countries"] if c["spot_avg_usd_lb"] is not None or c["trade_unit_usd_lb"] is not None)
    st.caption(f"Benchmarks, origin prices and forward-looking signals for an Atlanta roaster · {priced} of "
               f"{len(mat['countries'])} {'' if species == 'All' else species + ' '}origins priced.")

    cols = st.columns(4)
    curve = sig["curve"]["structure"]
    world = sig["crops"]["world"]
    curve_kpi = lambda c: kpi(c, "Term structure", value=curve["state"] if curve else "—",  # noqa: E731
                              sub=f"{curve['slope_pct']:+.1f}% front → {curve['months']} mo out" if curve else "loading",
                              foot=badge("Tight nearby supply" if curve and curve["state"] == "Backwardation" else
                                         "Ample nearby supply" if curve and curve["state"] == "Contango" else "No strong signal",
                                         "warn" if curve and curve["state"] == "Backwardation" else "pos"))
    world_kpi = lambda c, sp: kpi(c, f"World balance · USDA {world['market_year']}" if world else "World balance",  # noqa: E731
                                  value=f"{world['surplus_pct']:+.1f}%" if world and world["surplus_pct"] is not None else "—",
                                  sub="production vs consumption",
                                  foot=pill((world or {}).get("robusta_yoy_pct" if sp == "Robusta" else "arabica_yoy_pct"),
                                            f"{sp if sp != 'All' else 'Arabica'} crop y/y"))
    if species == "Arabica":
        kpi(cols[0], "ICE Arabica “C”", ice["usd_lb"], digits=4, foot=pill(ice["chg_1d_pct"], f"1d · settle {ice['date'] or '—'}"))
        kpi(cols[1], "30-day moving avg", ice["ma30_usd_lb"], digits=4, foot=pill(ice["chg_30d_pct"], "30d"))
        lo, hi = dual(ice["low_52w"]), dual(ice["high_52w"])
        kpi(cols[2], "52-week range", value=f"{lo[0].split('/')[0]}–{hi[0][1:]}", sub=f"{lo[1].split('/')[0]}–{hi[1][1:]}",
            foot=pill(ice["chg_1y_pct"], "1y"))
        curve_kpi(cols[3])
    elif species == "Robusta":
        if rob:
            kpi(cols[0], "ICE Robusta (London)", rob["usd_lb"], digits=4, foot=pill(rob["chg_1d_pct"], f"1d · {rob['date']}"))
            kpi(cols[1], "Robusta 30-day change", value=pct(rob["chg_30d_pct"]), sub="vs settle 30 days ago")
            lo, hi = dual(rob["low_52w"]), dual(rob["high_52w"])
            kpi(cols[2], "Robusta 52-week range", value=f"{lo[0].split('/')[0]}–{hi[0][1:]}", sub=f"{lo[1].split('/')[0]}–{hi[1][1:]}",
                foot=pill(rob["chg_1y_pct"], "1y"))
        else:
            with cols[0]:
                st.info("ICE Robusta has no free data feed. Upload daily settlements (`date,usd_tonne`) on **Data sources**.")
        world_kpi(cols[3], "Robusta")
    else:
        kpi(cols[0], "ICE Arabica “C”", ice["usd_lb"], digits=4, foot=pill(ice["chg_1d_pct"], "1d"))
        kpi(cols[1], "ICE Robusta", rob["usd_lb"] if rob else None, digits=4,
            foot=pill(rob["chg_1d_pct"], "1d") if rob else "Upload settlements on Data sources")
        spread = ice["usd_lb"] - rob["usd_lb"] if ice["usd_lb"] is not None and rob else None
        kpi(cols[2], "Arabica – Robusta spread", spread, digits=4, foot="arbitrage between the two contracts")
        world_kpi(cols[3], "All")

    # Benchmark history with range pills
    st.write("")
    with st.container(border=True):
        t, r = st.columns([3, 2], vertical_alignment="center")
        rng = r.segmented_control("Range", list(RANGE_LABELS), default="1y", key="ov_range", format_func=RANGE_LABELS.get,
                                  label_visibility="collapsed") or "1y"
        hist = q_bench(v, rng)
        stats = hist["robusta"] if species == "Robusta" else hist["arabica"]
        name = {"Robusta": "ICE Robusta", "All": "ICE Arabica “C” & Robusta"}.get(species, "ICE Arabica “C”")
        t.markdown(f"**{name}** <span style='opacity:.6'>{unit()} · {hist['interval']}</span>", unsafe_allow_html=True)
        if stats:
            t.markdown(f'<div class="op-row">{pill(stats["chg_pct"], "over " + RANGE_LABELS[rng])} · High {dual_text(stats["high"])}'
                       f' · Low {dual_text(stats["low"])}</div>', unsafe_allow_html=True)
        df = pd.DataFrame(hist["points"])
        if df.empty:
            st.info("Benchmarks are still loading.")
        else:
            df["date"] = pd.to_datetime(df["date"])
            fig = go.Figure()
            if species != "Robusta":
                fig.add_scatter(x=df["date"], y=df["arabica"] * k(), name="ICE Arabica “C”", line=dict(color=SERIES[0], width=2),
                                fill="tozeroy" if species == "Arabica" else None, fillcolor="rgba(217,119,6,0.08)",
                                customdata=(df["arabica"] * k(alt_unit())).values,
                                hovertemplate=f"%{{y:$.4f}}/{unit()[2:]} (%{{customdata:$.4f}}/{alt_unit()[2:]})")
            if species != "Arabica" and df["robusta"].notna().any():
                fig.add_scatter(x=df["date"], y=df["robusta"] * k(), name="ICE Robusta", line=dict(color=SERIES[3], width=2),
                                customdata=(df["robusta"] * k(alt_unit())).values,
                                hovertemplate=f"%{{y:$.4f}}/{unit()[2:]} (%{{customdata:$.4f}}/{alt_unit()[2:]})")
            chart_layout(fig, 300)
            if species == "Arabica":
                fig.update_yaxes(range=[float(df["arabica"].min() * k() * 0.95), float(df["arabica"].max() * k() * 1.03)])
            st.plotly_chart(fig, width="stretch")
            if rng == "max" and hist["earliest"]:
                st.caption(f"History since {hist['earliest']}.")

    left, right = st.columns([2, 1])
    with left:
        with st.container(border=True):
            a, b = st.columns([2, 2], vertical_alignment="center")
            a.markdown("**Origins at a glance**")
            with b:
                metric = segmented("Map metric", {"price": "Price", "diff": "Differential", "chg30": "30d change"}, "price", "ov_metric")
            world_map(mat["countries"], metric, key="ov_map", height=380)
            st.caption("Price is the average live spot offer, or the latest customs unit value where nothing is offered. "
                       "Click a country for its farms, lots and outlook.")
    with right:
        with st.container(border=True):
            st.markdown("**Latest alerts**")
            alerts = q_alerts(v)[:6]
            if not alerts:
                st.caption("No alerts yet.")
            for a in alerts:
                st.markdown(f"**{TITLES.get(a['alert_type'], a['alert_type'])}** · {a['created_at']:%b %d}  \n{a['message']}")

    st.subheader("Predictive signals & market outlook", anchor=False)
    st.caption("Leading indicators for where green prices are heading — curve, weather, exchange stocks and crop cycle.")
    c1, c2 = st.columns(2)
    with c1:
        signal_curve(sig["curve"])
        signal_stocks(sig["certified_stocks"])
    with c2:
        signal_weather(sig["weather"])
        signal_crops(sig["crops"])

    with st.expander("Origin currencies vs USD"):
        fx = pd.DataFrame(summ["fx"])
        if not fx.empty:
            st.dataframe(
                fx.rename(columns={"currency": "Currency", "date": "As of", "per_usd": "Per USD",
                                   "chg_7d_pct": "7d %", "chg_30d_pct": "30d %", "chg_1y_pct": "1y %"}),
                hide_index=True, width="stretch",
                column_config={c: st.column_config.NumberColumn(format="%+.2f%%") for c in ("7d %", "30d %", "1y %")},
            )
            st.caption("A positive change means the currency weakened (more per USD) — origin costs in USD tend to ease.")


def signal_curve(c: dict) -> None:
    with st.container(border=True):
        stt = c["structure"]
        h, b = st.columns([3, 2])
        h.markdown(f"**ICE “C” forward curve**  \n<span class='op-sub'>Settle by delivery month · {c['date'] or '—'}</span>",
                   unsafe_allow_html=True)
        if stt:
            tag = badge(f"{stt['state']} · {stt['slope_pct']:+.1f}%", "warn" if stt["state"] == "Backwardation" else "pos")
            b.markdown(f'<div style="text-align:right">{tag}</div>', unsafe_allow_html=True)
        if not c["contracts"]:
            st.caption("Forward curve not loaded — run the market job on Data sources.")
            return
        df = pd.DataFrame(c["contracts"])
        prior = {x["symbol"]: x["usd_lb"] for x in c["month_ago"]}
        fig = go.Figure()
        if prior:
            fig.add_scatter(x=df["label"], y=[prior.get(s, None) and prior[s] * k() for s in df["symbol"]], name="~1 month earlier",
                            line=dict(color="rgba(128,128,128,0.6)", width=2, dash="dash"), hovertemplate="%{y:$.4f}")
        fig.add_scatter(x=df["label"], y=df["usd_lb"] * k(), name=f"Settle {c['date']}", mode="lines+markers",
                        line=dict(color=SERIES[0], width=2), marker=dict(size=8),
                        customdata=(df["usd_lb"] * k(alt_unit())).values,
                        hovertemplate=f"%{{y:$.4f}}/{unit()[2:]} (%{{customdata:$.4f}}/{alt_unit()[2:]})")
        st.plotly_chart(chart_layout(fig, 200), width="stretch")
        if stt:
            st.caption(stt["takeaway"])


def signal_weather(w: list[dict]) -> None:
    with st.container(border=True):
        st.markdown("**Origin weather anomalies**  \n<span class='op-sub'>90-day rain vs 10-year normal, heat and frost "
                    "(incl. 10-day forecast) · Open-Meteo</span>", unsafe_allow_html=True)
        if not w:
            st.caption("Weather not loaded — run the weather job on Data sources.")
            return
        df = pd.DataFrame([{
            "Risk": f"{RISK_TONE.get(x['risk'], ('', '●'))[1]} {x['risk']}", "Belt": x["belt"], "Origin": x["country_iso"],
            "Species": x["species"], "Rain % normal": x["rain_pct_normal"], "Hot days 30d": x["hot_days_30d"],
            "Frost fcst": x["frost_forecast_days"], "Why": "within normal range" if x["risk"] == "Low" else " · ".join(x["reasons"]),
        } for x in w])
        st.dataframe(df, hide_index=True, width="stretch", height=260,
                     column_config={"Rain % normal": st.column_config.NumberColumn(format="%.0f%%"),
                                    "Why": st.column_config.TextColumn(width="large")})
        st.caption("Risk is a rule-of-thumb score from real observations, not a crop forecast.")


def signal_stocks(cs: dict) -> None:
    with st.container(border=True):
        st.markdown("**ICE certified stocks vs “C”**  \n<span class='op-sub'>Exchange-graded bags in licensed warehouses</span>",
                    unsafe_allow_html=True)
        if not cs["latest"]:
            st.caption("ICE publishes certified stocks daily but has no free API. Upload a `date,bags` CSV on Data sources.")
            return
        st.markdown(f'<div class="op-row"><b class="op-mono">{cs["latest"]["bags"]:,} bags</b> ({cs["latest"]["date"]}) · '
                    f'{pill(cs["chg_90d_pct"], "90 days")} · {badge("Supply pinch", "neg") if cs["pinch"] else badge("No pinch", "pos")}</div>',
                    unsafe_allow_html=True)
        df = pd.DataFrame(cs["series"])
        df["date"] = pd.to_datetime(df["date"])
        # Two aligned panels (bags above, price below) — never a dual-axis chart.
        from plotly.subplots import make_subplots

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06)
        fig.add_scatter(x=df["date"], y=df["bags"], name="Certified bags", line=dict(color=SERIES[2], width=2),
                        fill="tozeroy", fillcolor="rgba(5,150,105,0.1)", row=1, col=1, hovertemplate="%{y:,.0f} bags")
        fig.add_scatter(x=df["date"], y=df["ice_usd_lb"] * k(), name="ICE “C”", line=dict(color=SERIES[0], width=2),
                        row=2, col=1, hovertemplate="%{y:$.4f}")
        chart_layout(fig, 280)
        fig.update_yaxes(tickformat=",.0f", row=1, col=1)
        fig.update_yaxes(tickformat="$.2f", row=2, col=1)
        st.plotly_chart(fig, width="stretch")
        if cs.get("takeaway"):
            st.caption(cs["takeaway"])


def signal_crops(c: dict) -> None:
    with st.container(border=True):
        w = c["world"]
        h, b = st.columns([3, 2])
        h.markdown(f"**USDA FAS crop cycle**  \n<span class='op-sub'>Production, thousand 60 kg bags"
                   f"{' · ' + w['market_year'] if w else ''}</span>", unsafe_allow_html=True)
        if w and w["surplus_pct"] is not None:
            word = "World surplus" if w["surplus_pct"] >= 0 else "World deficit"
            tag = badge(f"{word} {w['surplus_pct']:+.1f}%", "pos" if w["surplus_pct"] >= 0 else "neg")
            b.markdown(f'<div style="text-align:right">{tag}</div>', unsafe_allow_html=True)
        if not c["origins"]:
            st.caption("USDA data not loaded — run the USDA job on Data sources.")
            return
        df = pd.DataFrame([{"Origin": o["name"], "Cycle": o["cycle"] or "—", "Crop M bags": o["production_k_bags"] / 1000,
                            "y/y %": o["yoy_pct"], "vs 5y avg %": o["vs_5y_avg_pct"]} for o in c["origins"][:8]])
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"Crop M bags": st.column_config.NumberColumn(format="%.1f"),
                                    "y/y %": st.column_config.NumberColumn(format="%+.1f%%"),
                                    "vs 5y avg %": st.column_config.NumberColumn(format="%+.1f%%")})
        st.caption("On/off-year is derived from the alternating Arabica crop (Arabica-majority origins only). "
                   "Surplus = world production − consumption.")


def page_matrix() -> None:
    v = data_version()
    head, tog = st.columns([3, 1.2], vertical_alignment="bottom")
    head.title("Global origin matrix", anchor=False)
    with tog:
        species = species_toggle("mx_species", "All")
    mat = q_matrix(v, species)
    bench = "ICE Robusta" if mat["benchmark"] == "robusta" else "ICE “C”"
    st.caption(f"Spot averages (importer offers), customs unit values and differential vs {bench} "
               f"({dual_text(mat['benchmark_usd_lb'], 4) if mat['benchmark_usd_lb'] is not None else 'not loaded'}). "
               "Click a country on the map for its lots and outlook.")
    metric = segmented("Map metric", {"price": "Price", "diff": "Differential", "chg30": "30d change"}, "price", "mx_metric")
    world_map(mat["countries"], metric, key="mx_map", height=380)

    f1, f2, f3, f4 = st.columns([2, 2, 2, 1.4])
    q = f1.text_input("Search country", placeholder="e.g. Ethiopia")
    region = f2.selectbox("Region", ["All regions", *REGIONS])
    harvest = f3.selectbox("Harvest status", ["Any", "In Harvest", "Fly Crop", "Main Crop Shipping", "Off-Season"])
    only = f4.toggle("Live offers only")

    rows = []
    for c in mat["countries"]:
        if q and q.lower() not in c["name"].lower() and q.upper() != c["iso_code"]:
            continue
        if region != "All regions" and c["region"] != region:
            continue
        if harvest != "Any" and c["harvest_status"] != harvest:
            continue
        if only and c["spot_avg_usd_lb"] is None:
            continue
        trade_note = None
        if c["trade_period"]:
            trade_note = (f"{c['trade_period'][:4]} annual" if c["trade_freq"] == "A" else f"3 mo to {c['trade_period'][:7]}") \
                + " · " + "/".join(c["trade_reporters"])
        rows.append({
            "Region": c["region"], "Country": c["name"], "Species": c["species_mix"],
            "spot": c["spot_avg_usd_lb"], "Offers": c["spot_offer_count"] or None,
            "Basis": c["spot_basis"], "SE lots": c["southeast_offer_count"] or None,
            "diff": c["diff_vs_c_usd_lb"], "Diff from": c["diff_basis"],
            "customs": c["trade_unit_usd_lb"], "Customs period": trade_note,
            "7d %": c["chg_7d_pct"], "30d %": c["chg_30d_pct"], "YoY %": c["chg_yoy_pct"],
            "Harvest": c["harvest_status"], "Calendar": harvest_strip(c["main_harvest_months"], c["fly_harvest_months"]),
        })
    df = pd.DataFrame(rows)
    st.caption(f"{len(df)} countries")
    if df.empty:
        return
    num = ["spot", "Offers", "SE lots", "diff", "customs", "7d %", "30d %", "YoY %"]
    df[num] = df[num].apply(pd.to_numeric, errors="coerce")
    txt = [c for c in df.columns if c not in num]
    df[txt] = df[txt].fillna("")
    df["_r"] = df["Region"].map({r: i for i, r in enumerate(REGIONS)})
    df = df.sort_values(["_r", "Country"]).drop(columns="_r")
    cfg = money_cols(df, {"spot": "Spot avg", "customs": "Customs"})
    for u in (unit(), alt_unit()):
        name = f"Diff {u}"
        df[name] = df["diff"] * k(u)
        cfg[name] = st.column_config.NumberColumn(name, format="$%+.2f")
    show = ["Region", "Country", "Species", *dual_names(["Spot avg"]), "Offers", "Basis", "SE lots", f"Diff {unit()}",
            f"Diff {alt_unit()}", "Diff from", *dual_names(["Customs"]), "Customs period", "7d %", "30d %", "YoY %",
            "Harvest", "Calendar"]
    st.dataframe(
        df[show], hide_index=True, width="stretch", height=620,
        column_config={**cfg,
                       "7d %": st.column_config.NumberColumn(format="%+.1f%%"), "30d %": st.column_config.NumberColumn(format="%+.1f%%"),
                       "YoY %": st.column_config.NumberColumn(format="%+.1f%%"),
                       "Calendar": st.column_config.TextColumn("Harvest calendar", help="Capital = main harvest month, lower-case = fly crop")},
    )
    st.caption("Customs unit values cover both species and all grades — a broad market signal, not a specialty price. "
               "Species filters average only that species' lots (untagged lots count as the origin's main species).")


def page_offers() -> None:
    v = data_version()
    rules = costing.load_rules()
    head, tog = st.columns([3, 1.2], vertical_alignment="bottom")
    head.title("Spot offers", anchor=False)
    with tog:
        species = species_toggle("of_species", "All")
    df = q_offers(v, species)
    if df.empty:
        st.info("No current importer offers yet. They load in the background on first start, or upload a spot sheet on Data Sources.")
        return
    f = st.columns([2, 2, 2, 1.3, 1.3])
    countries = f[0].multiselect("Country", sorted(df["Country"].dropna().unique()))
    processes = f[1].multiselect("Process", sorted(x for x in df["Process"].unique() if x))
    importers = f[2].multiselect("Importer", sorted(df["Importer"].unique()))
    min_score = f[3].number_input("Min score", 0.0, 100.0, 0.0, 0.5)
    se = f[4].toggle("Southeast only", help="Savannah, Charleston, Jacksonville warehouses")
    shrink = st.slider("Roast shrinkage", 0.10, 0.25, float(rules["default_shrinkage"]), 0.005, format="%.3f")

    d = df.copy()
    if countries:
        d = d[d["Country"].isin(countries)]
    if processes:
        d = d[d["Process"].isin(processes)]
    if importers:
        d = d[d["Importer"].isin(importers)]
    if min_score:
        d = d[d["Score"].fillna(0) >= min_score]
    if se:
        d = d[d["Southeast"]]
    d["roasted"] = d["landed"] / (1 - shrink)
    d = d.sort_values("landed")

    c1, c2, c3 = st.columns(3)
    kpi(c1, "Lots", value=f"{len(d)}", sub=f"{d['Importer'].nunique()} importers")
    kpi(c2, "Median landed ATL", None if d.empty else float(d["landed"].median()))
    kpi(c3, "Cheapest roasted", None if d.empty else float(d["roasted"].min()))
    st.write("")

    cfg = money_cols(d, {"spot": "Spot", "landed": "Landed ATL", "roasted": "Roasted"})
    d[f"Freight {unit()}"] = d["freight"] * k()
    show = ["Importer", "Country", "Lot", "Producer", "Species", "Process", "Score", "Bag kg", "Warehouse",
            *dual_names(["Spot"]), f"Freight {unit()}", *dual_names(["Landed ATL", "Roasted"]), "Bags avail.", "Link"]
    st.dataframe(
        d[show], hide_index=True, width="stretch", height=560,
        column_config={
            **cfg, "Lot": st.column_config.TextColumn(width="large"),
            f"Freight {unit()}": st.column_config.NumberColumn(format="$%.2f"),
            "Score": st.column_config.NumberColumn(format="%.1f"), "Bag kg": st.column_config.NumberColumn(format="%.1f"),
            "Link": st.column_config.LinkColumn("Link", display_text="open"),
        },
    )
    st.download_button("Download CSV", d[show].to_csv(index=False).encode(), "originpulse_offers.csv", "text/csv")
    st.caption(f"Landed = spot × (1 + tariff) + ocean surcharge + LTL freight to Atlanta + ${rules['warehouse_fee_usd_lb']:.2f}/lb "
               "warehouse & financing. Roasted = landed ÷ (1 − shrinkage).")


def page_trends() -> None:
    v = data_version()
    mat = q_matrix(v)
    names = {c["iso_code"]: c["name"] for c in mat["countries"]}
    priced = sorted(names, key=lambda i: (mat_lookup(mat, i)["spot_avg_usd_lb"] is None, names[i]))
    qp = (st.session_state.pop("trend_country", None) or st.query_params.get("country", "")).upper()
    default = qp if qp in names else (priced[0] if priced else "ETH")
    st.title("Historical trends", anchor=False)
    st.caption("Up to 5 years per origin: recorded importer spot prices vs ICE “C” (and ICE Robusta), the differential "
               "spread, and customs import unit values.")
    a, b, c = st.columns([2, 2, 2.4], vertical_alignment="bottom")
    iso = a.selectbox("Country", list(names), index=list(names).index(default), format_func=names.get)
    st.query_params["country"] = iso
    with c:
        range_ = segmented("Range", {"30d": "1M", "90d": "3M", "1y": "1Y", "3y": "3Y", "5y": "5Y"}, "1y", "tr_range")
    h0 = q_history(v, iso, None, range_)
    process = b.selectbox("Process", ["All processes", *h0["processes"]])
    h = h0 if process == "All processes" else q_history(v, iso, process, range_)
    row = mat_lookup(mat, iso)
    robusta = "Robusta" in (row.get("species_mix") or "")

    pts = pd.DataFrame(h["points"])
    if pts.empty:
        st.info("No price history yet.")
        return
    pts["date"] = pd.to_datetime(pts["date"])
    st.subheader(f"{names[iso]} spot average vs benchmark", anchor=False)
    fig = go.Figure()
    fig.add_scatter(x=pts["date"], y=pts["ice_c_usd_lb"] * k(), name="ICE “C”", line=dict(color=SERIES[0], width=2),
                    hovertemplate="%{y:$.4f}")
    if robusta and pts["ice_robusta_usd_lb"].notna().any():
        fig.add_scatter(x=pts["date"], y=pts["ice_robusta_usd_lb"] * k(), name="ICE Robusta",
                        line=dict(color=SERIES[3], width=2), hovertemplate="%{y:$.4f}")
    sparse = pts["spot_avg_usd_lb"].notna().sum() < 10
    fig.add_scatter(x=pts["date"], y=pts["spot_avg_usd_lb"] * k(), name=f"{names[iso]} spot avg", connectgaps=True,
                    mode="lines+markers" if sparse else "lines", marker=dict(size=8),
                    line=dict(color=SERIES[1], width=2), customdata=(pts["spot_avg_usd_lb"] * k(alt_unit())).values,
                    hovertemplate=f"%{{y:$.2f}}/{unit()[2:]} (%{{customdata:$.2f}}/{alt_unit()[2:]})")
    st.plotly_chart(chart_layout(fig), width="stretch")
    basis = pts["spot_basis"].dropna()
    if not basis.empty and basis.iloc[-1] == "small packs":
        st.caption("Spot average is from small packs (20–22 lb boxes), which carry a small-lot premium.")

    d = pts[pts["diff_usd_lb"].notna()]
    st.subheader("Differential spread", anchor=False)
    trend = h["spread_trend"]
    if trend:
        st.caption(f"Spread is **{trend['direction']}** ({dual_text(trend['usd_lb_per_30d'], 3, True)} per 30 days).")
    if d.empty:
        st.caption("Needs live spot offers for this origin.")
    else:
        fig = go.Figure(go.Scatter(x=d["date"], y=d["diff_usd_lb"] * k(), name="Differential",
                                   line=dict(color=SERIES[1], width=2), hovertemplate="%{y:$+.2f}"))
        fig.add_hline(y=0, line_color="rgba(128,128,128,0.6)", line_width=1)
        st.plotly_chart(chart_layout(fig, 240, "$+.2f"), width="stretch")

    tr = pd.DataFrame(h["trade"])
    st.subheader("Customs import unit values", anchor=False)
    if tr.empty:
        st.caption("No customs data for this origin in this range yet.")
    else:
        tr["x"] = pd.to_datetime(tr.apply(lambda r: f"{r['period'][:4]}-07-01" if r["freq"] == "A" else r["period"], axis=1))
        tr["price"] = tr["unit_usd_lb"] * k()
        fig = go.Figure()
        for i, rep in enumerate(sorted(tr["reporter"].unique())):
            col = SERIES[i % len(SERIES)]
            m = tr[(tr["reporter"] == rep) & (tr["freq"] == "M")].sort_values("x")
            a_ = tr[(tr["reporter"] == rep) & (tr["freq"] == "A")].sort_values("x")
            if not m.empty:
                fig.add_scatter(x=m["x"], y=m["price"], name=f"{rep} monthly", mode="lines+markers", line=dict(color=col, width=2),
                                marker=dict(size=7), hovertemplate="%{y:$.2f}")
            if not a_.empty:
                fig.add_scatter(x=a_["x"], y=a_["price"], name=f"{rep} annual", mode="lines+markers",
                                line=dict(color=col, width=2, dash="dash"), opacity=0.6, hovertemplate="%{y:$.2f}")
        st.plotly_chart(chart_layout(fig, 280), width="stretch")
        st.caption("Solid = monthly (3-month window), dashed = annual average plotted mid-year.")
    with st.expander("Table view"):
        st.dataframe(pts, hide_index=True, width="stretch")


def mat_lookup(mat: dict, iso: str) -> dict:
    return next(c for c in mat["countries"] if c["iso_code"] == iso)


def page_calculator() -> None:
    rules = costing.load_rules()
    st.title("Atlanta landed-cost simulator", anchor=False)
    st.caption("Landed green = spot × (1 + tariff) + ocean surcharge + LTL freight to Atlanta + warehouse/financing fee. "
               f"True roasted = landed ÷ (1 − shrinkage). Destination: {rules['destination']}")
    warehouses = [w["name"] for t in rules["tiers"] for w in t["warehouses"]]

    a, b = st.columns([1, 1])
    with a:
        with st.container(border=True):
            st.markdown("**Offer**")
            price_unit = st.radio("Price quoted per", ["lb", "kg", "bag"], horizontal=True)
            bag_kg = st.selectbox("Bag size (kg)", list(STANDARD_BAG_KG), index=1)
            price = st.number_input(f"Spot price ($ per {price_unit})", min_value=0.01,
                                    value={"lb": 4.00, "kg": 8.82, "bag": 608.48}[price_unit], step=0.05)
            wh = st.selectbox("Warehouse", [*warehouses, "Other / unknown"])
            match = costing.match_warehouse(None if wh == "Other / unknown" else wh, rules)
            freight = st.number_input("LTL freight to Atlanta ($/lb)", 0.0, 2.0, float(match.freight_usd_lb), 0.01,
                                      help=f"{match.tier_label} tier")
            fee = st.number_input("Warehouse & financing fee ($/lb)", 0.0, 1.0, float(rules["warehouse_fee_usd_lb"]), 0.01)
            shrink = st.slider("Roast shrinkage", 0.10, 0.25, float(rules["default_shrinkage"]), 0.005, format="%.3f")
            bags = st.number_input("Bags in order (optional)", 0, 1000, 10)
    with b:
        with st.container(border=True):
            st.markdown("**Tariff & ocean freight friction**")
            st.caption("For offers quoted before duty or FOB origin. Importer spot prices are usually duty-paid and "
                       "ex-warehouse — leave at 0 for those.")
            tariff = st.number_input("Tariff / import duty (% of spot value)", 0.0, 200.0,
                                     float(rules.get("tariff_pct", 0)) * 100, 0.5)
            basis = st.segmented_control("Ocean surcharge basis", ["$/container", "$/lb"], default="$/container") or "$/container"
            if basis == "$/container":
                o1, o2 = st.columns(2)
                per_box = o1.number_input("Ocean surcharge ($ per 20′ container)", 0.0, 50000.0, 0.0, 50.0)
                box_bags = o2.number_input("Bags per container", 1, 1000, costing.CONTAINER_BAGS)
                ocean = costing.container_to_usd_lb(per_box, int(box_bags), float(bag_kg)) if per_box else 0.0
                if ocean:
                    st.caption(f"= {dual_text(ocean, 4)}")
            else:
                ocean = st.number_input("Ocean surcharge ($/lb)", 0.0, 2.0, float(rules.get("ocean_surcharge_usd_lb", 0)), 0.01)

    spot_lb = price / (bag_kg * LB_PER_KG) if price_unit == "bag" else (price / LB_PER_KG if price_unit == "kg" else price)
    bd, _ = costing.cost_breakdown(spot_lb, None if wh == "Other / unknown" else wh, rules=rules, freight_override=freight,
                                   fee_override=fee, shrinkage=shrink, tariff_pct=tariff / 100, ocean_usd_lb=ocean)
    st.write("")
    m = st.columns(4)
    kpi(m[0], "Spot (green)", bd.spot_usd_lb)
    kpi(m[1], "Tariff + ocean friction", bd.tariff_usd_lb + bd.ocean_usd_lb)
    kpi(m[2], "Landed green · Atlanta", bd.landed_usd_lb)
    kpi(m[3], f"True roasted · {shrink * 100:.1f}% shrink", bd.roasted_usd_lb)

    lines = [("Spot (green)", bd.spot_usd_lb), (f"Tariff ({tariff:.1f}%)", bd.tariff_usd_lb),
             ("Ocean freight surcharge", bd.ocean_usd_lb), ("LTL freight to Atlanta", bd.freight_usd_lb),
             ("Warehouse / financing fee", bd.fee_usd_lb), ("Landed green cost", bd.landed_usd_lb),
             ("True roasted cost", bd.roasted_usd_lb)]
    lbs = bags * bag_kg * LB_PER_KG if bags else 0
    tbl = pd.DataFrame([{"Component": n, f"Per {unit()[2:]}": x * k(), f"Per {alt_unit()[2:]}": x * k(alt_unit()),
                         "Order total": (x * lbs if lbs and "roasted" not in n.lower() else None)} for n, x in lines])
    st.write("")
    st.dataframe(tbl, hide_index=True, width="stretch",
                 column_config={f"Per {unit()[2:]}": st.column_config.NumberColumn(format="$%.4f"),
                                f"Per {alt_unit()[2:]}": st.column_config.NumberColumn(format="$%.4f"),
                                "Order total": st.column_config.NumberColumn(format="$%,.2f")})
    if lbs:
        st.caption(f"Order: {bags} × {bag_kg:g} kg = {lbs:,.0f} lb green → {lbs * (1 - shrink):,.0f} lb roasted.")

    st.subheader("Same lot landed from each warehouse", anchor=False)
    comp = []
    for t in rules["tiers"]:
        for w in t["warehouses"]:
            b2, _ = costing.cost_breakdown(spot_lb, w["name"], rules=rules, fee_override=fee, shrinkage=shrink,
                                           tariff_pct=tariff / 100, ocean_usd_lb=ocean)
            comp.append({"Warehouse": w["name"], "Tier": t["label"], "Freight": b2.freight_usd_lb * k(),
                         "Landed": b2.landed_usd_lb * k(), "Roasted": b2.roasted_usd_lb * k()})
    cdf = pd.DataFrame(comp).sort_values("Landed")
    fig = px.bar(cdf, x="Landed", y="Warehouse", color="Tier", orientation="h", color_discrete_sequence=SERIES,
                 hover_data={"Freight": ":$.2f", "Roasted": ":$.2f", "Landed": ":$.2f"})
    fig.update_layout(height=40 + 32 * len(cdf), margin=dict(l=8, r=8, t=8, b=8), bargap=0.25,
                      legend=dict(orientation="h", y=1.1, title=None), xaxis_tickformat="$.2f",
                      yaxis=dict(categoryorder="total descending", title=None), xaxis_title=f"Landed {unit()}",
                      xaxis_range=[float(cdf["Landed"].min()) * 0.97, float(cdf["Landed"].max()) * 1.01])
    st.plotly_chart(fig, width="stretch")

    with st.expander("Freight rules"):
        tiers = pd.DataFrame([{"id": t["id"], "Tier": t["label"], "Low": t["low"], "Default": t["default"], "High": t["high"],
                               "Warehouses": ", ".join(w["name"] for w in t["warehouses"])} for t in rules["tiers"]])
        if not admin_gate("Editing freight rules"):
            st.dataframe(tiers.drop(columns="id"), hide_index=True, width="stretch")
            return
        edited = st.data_editor(tiers, hide_index=True, width="stretch", disabled=["id", "Tier", "Warehouses"],
                                column_config={c: st.column_config.NumberColumn(format="$%.2f", step=0.01)
                                               for c in ("Low", "Default", "High")})
        e = st.columns(5)
        new_fee = e[0].number_input("Default fee $/lb", 0.0, 1.0, float(rules["warehouse_fee_usd_lb"]), 0.01, key="r_fee")
        new_unknown = e[1].number_input("Unknown warehouse freight $/lb", 0.0, 2.0,
                                        float(rules["unknown_warehouse_freight_usd_lb"]), 0.01, key="r_unk")
        new_shrink = e[2].number_input("Default shrinkage", 0.0, 0.5, float(rules["default_shrinkage"]), 0.005, key="r_sh")
        new_tariff = e[3].number_input("Default tariff %", 0.0, 200.0, float(rules.get("tariff_pct", 0)) * 100, 0.5, key="r_tar")
        new_ocean = e[4].number_input("Default ocean $/lb", 0.0, 2.0, float(rules.get("ocean_surcharge_usd_lb", 0)), 0.01, key="r_oc")
        if st.button("Save freight rules", type="primary"):
            new = {**rules, "warehouse_fee_usd_lb": new_fee, "unknown_warehouse_freight_usd_lb": new_unknown,
                   "default_shrinkage": new_shrink, "tariff_pct": new_tariff / 100, "ocean_surcharge_usd_lb": new_ocean,
                   "tiers": [dict(t) for t in rules["tiers"]]}
            for t, (_, r) in zip(new["tiers"], edited.iterrows()):
                t.update(low=float(r["Low"]), default=float(r["Default"]), high=float(r["High"]))
            try:
                costing.save_rules(new)
                st.success("Saved. Default tariff and ocean surcharge apply to newly ingested offers.")
                st.rerun()
            except (ValueError, KeyError) as e:
                st.error(str(e))


def page_alerts() -> None:
    v = data_version()
    st.title("Alerts", anchor=False)
    alerts = q_alerts(v)
    a, b, c = st.columns([2, 1, 1])
    types = a.multiselect("Type", ALERT_TYPES, format_func=lambda t: TITLES.get(t, t))
    unread_only = b.toggle("Unread only")
    if c.button("Mark all read", disabled=not any(not x["is_read"] for x in alerts)):
        with session_scope() as s:
            for x in s.exec(select(Alert).where(Alert.is_read.is_(False))).all():
                x.is_read = True
                s.add(x)
            s.commit()
        st.rerun()
    shown = [x for x in alerts if (not types or x["alert_type"] in types) and (not unread_only or not x["is_read"])]
    if not shown:
        st.info("No alerts match. The engine runs after each data refresh: origin drops >"
                f"{config.ALERT_ORIGIN_DROP_PCT:g}%/{config.ALERT_ORIGIN_DROP_DAYS}d, {config.ALERT_MIN_SCORE:g}+ lots under "
                f"their 30-day average, Southeast arrivals, and FX weakening >{config.ALERT_FX_WEAKEN_PCT:g}%/week.")
    for x in shown:
        with st.container(border=True):
            l, r = st.columns([6, 1])
            dot = "" if x["is_read"] else "🟠 "
            l.markdown(f"{dot}**{TITLES.get(x['alert_type'], x['alert_type'])}** · {x['created_at']:%b %d, %H:%M} UTC  \n{x['message']}")
            if not x["is_read"] and r.button("Mark read", key=f"read{x['id']}"):
                with session_scope() as s:
                    obj = s.get(Alert, x["id"])
                    obj.is_read = True
                    s.add(obj)
                    s.commit()
                st.rerun()
    with st.expander("Digest text"):
        with session_scope() as s:
            recent = s.exec(select(Alert).where(Alert.created_at >= datetime.now(timezone.utc) - timedelta(days=7))
                            .order_by(Alert.created_at.desc())).all()
            st.code(build_digest(list(recent), analytics.benchmark_summary(s)), language=None)


def page_data() -> None:
    v = data_version()
    st.title("Data sources", anchor=False)
    busy = running_jobs()
    if busy:
        st.info(f"Running now: {', '.join(busy)}. This page updates when they finish.", icon="⏳")

    runs, files = q_runs(v)
    with session_scope() as s:
        counts = {
            "Benchmark days": s.exec(select(func.count(MarketBenchmark.id))).one(),
            "Customs rows": s.exec(select(func.count(TradeValuation.id))).one(),
            "Current offers": len(analytics.current_offers(s)),
            "Weather belts": s.exec(select(func.count(func.distinct(WeatherSignal.belt)))).one(),
            "Alerts": s.exec(select(func.count(Alert.id))).one(),
        }
    cols = st.columns(len(counts))
    for col, (label, n) in zip(cols, counts.items()):
        kpi(col, label, value=f"{n:,}")

    st.subheader("Run a pipeline", anchor=False)
    st.caption("Most jobs take seconds. Customs data is slow (rate-limited API) and runs in the background.")
    if admin_gate("Running pipelines"):
        job_list = [("benchmarks", "ICE & FX"), ("market", "Forward curve"), ("weather", "Weather"), ("usda", "USDA crops"),
                    ("scrapers", "Importer catalogs"), ("dropzone", "Spot-sheet folder"), ("alerts", "Alert engine"),
                    ("trade", "Customs data")]
        bc = st.columns(4)
        for i, (job, label) in enumerate(job_list):
            if bc[i % 4].button(label, width="stretch", disabled=job in busy, key=f"job_{job}"):
                if job == "trade":
                    _bg(lambda: (jobs.job_trade(), jobs.job_alerts(deliver=False)), "trade")
                    st.toast("Customs refresh started in the background.")
                else:
                    with st.spinner(f"Running {label}…"):
                        res = jobs.job_alerts(deliver=False) if job == "alerts" else jobs.JOBS[job]()
                    st.toast(f"{label}: {res.get('status', 'done') if isinstance(res, dict) else 'done'}")
                st.rerun()

    st.subheader("Upload spot sheets", anchor=False)
    st.caption("CSV, Excel or PDF offer lists from importers. Name files like `atlas_2026-11-14.pdf` to tag the importer and date.")
    if admin_gate("Uploading spot sheets"):
        ups = st.file_uploader("Spot sheets", type=["csv", "xlsx", "xls", "pdf"], accept_multiple_files=True,
                               label_visibility="collapsed")
        if ups and st.button("Ingest files", type="primary"):
            for up in ups:
                try:
                    path = spot.save_upload(up.name, up.getvalue())
                    with session_scope() as s:
                        res = spot.ingest_file(s, path)
                    st.write(f"**{up.name}** — {res.get('status')}: {res.get('rows', 0)} rows {res.get('message', '')}")
                except ValueError as e:
                    st.error(f"{up.name}: {e}")
            jobs.job_alerts(deliver=False)

    st.subheader("Exchange data without a free feed", anchor=False)
    st.caption("ICE Robusta settlements and ICE certified stocks aren't available from a free API. Upload them as CSV.")
    if admin_gate("Uploading exchange data"):
        u1, u2 = st.columns(2)
        for col, kind, label, hint in ((u1, "robusta", "ICE Robusta settlements", "date,usd_tonne"),
                                       (u2, "certified_stocks", "ICE certified stocks", "date,bags")):
            f = col.file_uploader(f"{label} (`{hint}`)", type=["csv"], key=f"up_{kind}")
            if f and col.button(f"Load {label}", key=f"load_{kind}"):
                try:
                    with session_scope() as s:
                        res = market.import_csv(s, kind, f.getvalue())
                    col.success(f"Loaded {res['rows']} rows.")
                except (ValueError, KeyError) as e:
                    col.error(str(e))

    st.subheader("Recent runs", anchor=False)
    if runs:
        rdf = pd.DataFrame(runs)[["job", "started_at", "finished_at", "status", "rows", "message"]]
        st.dataframe(rdf, hide_index=True, width="stretch", height=300,
                     column_config={"message": st.column_config.TextColumn(width="large")})
    if files:
        st.subheader("Ingested files", anchor=False)
        st.dataframe(pd.DataFrame(files)[["filename", "importer", "ingested_at", "rows", "status", "message"]],
                     hide_index=True, width="stretch")

    with st.expander("Source configuration"):
        st.write({
            "Importer catalogs (enabled)": config.ENABLED_SCRAPERS,
            "Importer catalogs (available)": sorted(SCRAPERS),
            "Comtrade API key": bool(config.COMTRADE_API_KEY),
            "Census API key": bool(os.getenv("CENSUS_API_KEY")),
            "Claude PDF extraction": bool(config.ANTHROPIC_API_KEY),
            "Database": config.DATABASE_URL.split("://", 1)[0],
        })
        st.caption("On Streamlit Community Cloud the disk is temporary: data is rebuilt after the app restarts or sleeps. "
                   "Set DATABASE_URL to a hosted PostgreSQL database to keep history (see README).")


# ---------------------------------------------------------------- shell

def sidebar() -> None:
    with st.sidebar:
        st.radio("Primary unit", ["$/lb", "$/kg"], horizontal=True, key="unit",
                 help="Prices show in both units; this picks which one is bold.")
        busy = running_jobs()
        if busy:
            st.caption(f"⏳ Loading in background: {', '.join(busy)}")
        with session_scope() as s:
            last = s.exec(select(IngestRun.finished_at).where(IngestRun.status.in_(["ok", "partial"]))
                          .order_by(IngestRun.finished_at.desc())).first()
        if last:
            st.caption(f"Last data refresh: {last:%b %d %H:%M} UTC")
        if st.button("Refresh view", width="stretch"):
            st.cache_data.clear()
            st.rerun()
        if os.getenv("ADMIN_PASSWORD") and not st.session_state.get("admin_ok"):
            pw = st.text_input("Admin password", type="password")
            if pw:
                import hmac
                st.session_state["admin_ok"] = hmac.compare_digest(pw, os.getenv("ADMIN_PASSWORD", ""))
                if not st.session_state["admin_ok"]:
                    st.error("Wrong password")
                else:
                    st.rerun()


TRENDS_PAGE = st.Page(page_trends, title="Trends", icon=":material/show_chart:", url_path="trends")


def main() -> None:
    st.html(CSS + f"<style>:root{{{VARS_DARK if is_dark() else VARS_LIGHT}}}</style>")
    with st.spinner("First start: loading ICE “C” history and FX prices… (about 30 seconds)"):
        bootstrap()
    refresh_if_stale()
    sidebar()
    pages = [
        st.Page(page_overview, title="Overview", icon=":material/public:", url_path="overview", default=True),
        st.Page(page_matrix, title="Origin matrix", icon=":material/grid_view:", url_path="origins"),
        st.Page(page_offers, title="Spot offers", icon=":material/inventory_2:", url_path="offers"),
        TRENDS_PAGE,
        st.Page(page_calculator, title="Landed cost", icon=":material/calculate:", url_path="calculator"),
        st.Page(page_alerts, title="Alerts", icon=":material/notifications:", url_path="alerts"),
        st.Page(page_data, title="Data sources", icon=":material/database:", url_path="data"),
    ]
    st.navigation(pages).run()


main()
