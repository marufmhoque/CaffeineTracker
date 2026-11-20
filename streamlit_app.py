"""OriginPulse on Streamlit — green coffee price intelligence for an Atlanta specialty roaster.

Run locally:   streamlit run streamlit_app.py
Deploy:        Streamlit Community Cloud → New app → main file `streamlit_app.py` (see README).

This reuses the FastAPI backend's pipelines, analytics and cost engine directly (no API server).
On a fresh container the database is empty, so the app backfills ICE "C"/FX synchronously and
loads importer offers + customs data in a background thread, then refreshes stale data on visits.
"""

from __future__ import annotations

import os
import threading
from datetime import date, datetime, timedelta, timezone

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

from backend.app import analytics, config, costing, jobs  # noqa: E402
from backend.app.alerts import ALERT_TYPES, TITLES, build_digest  # noqa: E402
from backend.app.db import init_db, last_successful_run, session_scope  # noqa: E402
from backend.app.models import Alert, Country, IngestedFile, IngestRun, MarketBenchmark, SpotOffer, TradeValuation  # noqa: E402
from backend.app.pipelines import spot  # noqa: E402
from backend.app.units import LB_PER_KG, STANDARD_BAG_KG  # noqa: E402
from scrapers import SCRAPERS  # noqa: E402

st.set_page_config(page_title="OriginPulse", page_icon="☕", layout="wide")

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
SEQ_LIGHT = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SEQ_DARK = ["#104281", "#184f95", "#256abf", "#2a78d6", "#5598e7", "#86b6ef", "#b7d3f6"]
DIVERGING = ["#0ca30c", "#e8e8e6", "#d03b3b"]  # cheaper (green) ← 0 → dearer (red), buyer's view
REGIONS = ["Central America & Mexico", "South America", "Caribbean", "East & Central Africa", "West Africa", "Asia & Oceania"]
MONTHS = "JFMAMJJASOND"
REFRESH_EVERY = {"benchmarks": timedelta(hours=12), "scrapers": timedelta(hours=12), "trade": timedelta(days=7)}


# ---------------------------------------------------------------- bootstrap & refresh

def _bg(fn, name: str) -> None:
    threading.Thread(target=fn, daemon=True, name=f"op-{name}").start()


def _background_load(need_scrape: bool, need_trade: bool) -> None:
    if need_scrape:
        jobs.job_scrapers()
    jobs.job_dropzone()
    jobs.job_alerts(deliver=False)
    if need_trade:
        jobs.job_trade()  # slowest (Comtrade rate limits): ~2–4 min on an empty database
        jobs.job_alerts(deliver=False)


@st.cache_resource(show_spinner=False)
def bootstrap() -> dict:
    """Runs once per server process: schema, seed data, and first data load."""
    init_db()
    costing.load_rules()
    config.SPOT_SHEETS_DIR.mkdir(parents=True, exist_ok=True)
    with session_scope() as s:
        no_bench = s.exec(select(MarketBenchmark.id).limit(1)).first() is None
        no_offers = s.exec(select(SpotOffer.id).limit(1)).first() is None
        no_trade = s.exec(select(TradeValuation.id).limit(1)).first() is None
    if no_bench:
        jobs.job_benchmarks()  # ~20 quick Yahoo requests; the dashboard needs ICE "C" to be useful
    if no_offers or no_trade:
        _bg(lambda: _background_load(no_offers, no_trade), "bootstrap")
    return {"started": datetime.now(timezone.utc), "cold_start": no_bench}


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
def q_matrix(v) -> dict:
    with session_scope() as s:
        return analytics.country_matrix(s)


@st.cache_data(ttl=600, show_spinner=False)
def q_summary(v) -> dict:
    with session_scope() as s:
        return analytics.benchmark_summary(s)


@st.cache_data(ttl=600, show_spinner=False)
def q_ice(v, days: int) -> pd.DataFrame:
    with session_scope() as s:
        rows = analytics.ice_series(s, date.today() - timedelta(days=days))
    return pd.DataFrame(rows, columns=["date", "usd_lb"])


@st.cache_data(ttl=600, show_spinner=False)
def q_offers(v) -> pd.DataFrame:
    with session_scope() as s:
        names = {c.iso_code: c.name for c in s.exec(select(Country)).all()}
        rows = analytics.current_offers(s)
        out = [{
            "Importer": o.importer, "Country": names.get(o.country_iso or "", o.country_iso or "—"),
            "iso": o.country_iso, "Lot": o.lot_name, "Process": o.process, "Variety": o.variety,
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
        df[["Process", "Variety", "Warehouse"]] = df[["Process", "Variety", "Warehouse"]].fillna("")
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


def k() -> float:
    """Multiplier from $/lb to the selected display unit."""
    return LB_PER_KG if unit() == "$/kg" else 1.0


def money(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"${v * k():,.{digits}f}"


def signed(v: float | None) -> str:
    return "—" if v is None else f"{'+' if v >= 0 else '−'}${abs(v * k()):,.2f}"


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def seq_scale() -> list[str]:
    try:
        dark = st.context.theme.type == "dark"
    except Exception:  # noqa: BLE001
        dark = False
    return SEQ_DARK if dark else SEQ_LIGHT


def money_col(label: str, digits: int = 2):
    return st.column_config.NumberColumn(f"{label} {unit()}", format=f"$%.{digits}f")


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


def line_layout(fig: go.Figure, height: int = 320, yfmt: str = "$.2f") -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8), hovermode="x unified",
                      legend=dict(orientation="h", y=1.08, x=0, title=None), yaxis_tickformat=yfmt)
    fig.update_xaxes(showgrid=False, title=None)
    fig.update_yaxes(title=None)
    return fig


# ---------------------------------------------------------------- map

def world_map(rows: list[dict], metric: str, height: int = 430) -> None:
    df = pd.DataFrame(rows)
    if metric == "price":
        col, title = "spot_avg_usd_lb", f"Spot avg {unit()}"
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(pd.to_numeric(df["trade_unit_usd_lb"], errors="coerce"))  # fall back to customs value where no live lots
        title += " (customs where no spot)"
    elif metric == "diff":
        col, title = "diff_vs_c_usd_lb", f"Diff vs “C” {unit()}"
    else:
        col, title = "chg_30d_pct", "30-day change %"
    df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df[col].notna()].copy()
    if df.empty:
        st.caption("No data for this metric yet.")
        return
    if metric != "chg30":
        df[col] = df[col] * k()
    # Clip the color range to the 5th–95th percentile so one boutique lot doesn't wash out every other country.
    lo, hi = df[col].quantile(0.05), df[col].quantile(0.95)
    hover = {"iso_code": False, col: ":.2f", "region": True}
    if metric == "chg30":
        m = max(abs(lo), abs(hi), 1)
        fig = px.choropleth(df, locations="iso_code", color=col, hover_name="name", hover_data=hover,
                            color_continuous_scale=DIVERGING, range_color=(-m, m), labels={col: title})
    else:
        fig = px.choropleth(df, locations="iso_code", color=col, hover_name="name", hover_data=hover,
                            color_continuous_scale=seq_scale(), range_color=(lo, hi if hi > lo else lo + 1),
                            labels={col: title})
    fig.update_geos(showframe=False, showcoastlines=False, projection_type="natural earth",
                    showcountries=True, countrycolor="rgba(128,128,128,0.35)",
                    showland=True, landcolor="rgba(128,128,128,0.12)", bgcolor="rgba(0,0,0,0)",
                    lataxis_range=[-45, 50])
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=0, b=0),
                      coloraxis_colorbar=dict(title=title, thickness=10, len=0.6))
    st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------- pages

def page_overview() -> None:
    v = data_version()
    summ = q_summary(v)
    mat = q_matrix(v)
    ice = summ["ice_arabica"]
    st.title("Overview")
    st.caption(f"Green coffee prices across {len(mat['countries'])} producing countries · landed into Atlanta, GA")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("ICE Arabica “C”", money(ice["usd_lb"], 4), pct(ice["chg_1d_pct"]) + " 1d" if ice["chg_1d_pct"] is not None else None,
              delta_color="inverse", help=f"Front-month settle, {ice['date'] or '—'}")
    c2.metric("30-day average", money(ice["ma30_usd_lb"], 4))
    c3.metric("52-week range", f"{money(ice['low_52w'])} – {money(ice['high_52w'])}")
    c4.metric("1-year change", pct(ice["chg_1y_pct"]), delta=None)

    left, right = st.columns([3, 2])
    with left:
        st.subheader("ICE “C”, last 12 months")
        df = q_ice(v, 365)
        if df.empty:
            st.info("Benchmarks are still loading.")
        else:
            df["price"] = df["usd_lb"] * k()
            fig = px.line(df, x="date", y="price", color_discrete_sequence=SERIES)
            fig.update_traces(line_width=2, hovertemplate="%{y:$.4f}")
            st.plotly_chart(line_layout(fig, 280, "$.2f"), width="stretch")
    with right:
        st.subheader("Latest alerts")
        alerts = q_alerts(v)[:6]
        if not alerts:
            st.caption("No alerts yet.")
        for a in alerts:
            st.markdown(f"**{TITLES.get(a['alert_type'], a['alert_type'])}** · {a['created_at']:%b %d}  \n{a['message']}")

    st.subheader("Origin prices")
    metric = st.segmented_control("Map metric", ["price", "diff", "chg30"], default="price", key="ov_metric",
                                  format_func={"price": "Price", "diff": "Differential", "chg30": "30d change"}.get)
    world_map(mat["countries"], metric or "price")

    st.subheader("Origin currencies vs USD")
    fx = pd.DataFrame(summ["fx"])
    if not fx.empty:
        st.dataframe(
            fx.rename(columns={"currency": "Currency", "date": "As of", "per_usd": "Per USD",
                               "chg_7d_pct": "7d %", "chg_30d_pct": "30d %", "chg_1y_pct": "1y %"}),
            hide_index=True, width="stretch",
            column_config={c: st.column_config.NumberColumn(format="%+.2f%%") for c in ("7d %", "30d %", "1y %")},
        )
        st.caption("A positive change means the currency weakened (more per USD) — origin costs in USD tend to ease.")


def page_matrix() -> None:
    v = data_version()
    mat = q_matrix(v)
    st.title("Global origin matrix")
    st.caption(f"Spot averages (importer offers), customs unit values and differential vs ICE “C” "
               f"({money(mat['ice_arabica_usd_lb'], 4)} {unit()}).")
    metric = st.segmented_control("Map metric", ["price", "diff", "chg30"], default="price", key="mx_metric",
                                  format_func={"price": "Price", "diff": "Differential", "chg30": "30d change"}.get)
    world_map(mat["countries"], metric or "price", 380)

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
        sc = lambda x: None if x is None else x * k()  # noqa: E731
        trade_note = None
        if c["trade_period"]:
            trade_note = (f"{c['trade_period'][:4]} annual" if c["trade_freq"] == "A" else f"3 mo to {c['trade_period'][:7]}") \
                + " · " + "/".join(c["trade_reporters"])
        rows.append({
            "Region": c["region"], "Country": c["name"], "Species": c["species_mix"],
            "Spot avg": sc(c["spot_avg_usd_lb"]),
            "Offers": c["spot_offer_count"] or None,
            "Basis": c["spot_basis"], "SE lots": c["southeast_offer_count"] or None,
            "Diff vs C": sc(c["diff_vs_c_usd_lb"]), "Diff from": c["diff_basis"],
            "Customs": sc(c["trade_unit_usd_lb"]), "Customs period": trade_note,
            "7d %": c["chg_7d_pct"], "30d %": c["chg_30d_pct"], "YoY %": c["chg_yoy_pct"],
            "Harvest": c["harvest_status"], "Calendar": harvest_strip(c["main_harvest_months"], c["fly_harvest_months"]),
            "Trends": f"/trends?country={c['iso_code']}",
        })
    df = pd.DataFrame(rows)
    st.caption(f"{len(df)} countries")
    if df.empty:
        return
    num = ["Spot avg", "Offers", "SE lots", "Diff vs C", "Customs", "7d %", "30d %", "YoY %"]
    df[num] = df[num].apply(pd.to_numeric, errors="coerce")
    txt = [c for c in df.columns if c not in num]
    df[txt] = df[txt].fillna("")
    df["_r"] = df["Region"].map({r: i for i, r in enumerate(REGIONS)})
    df = df.sort_values(["_r", "Country"]).drop(columns="_r")
    st.dataframe(
        df, hide_index=True, width="stretch", height=620,
        column_config={
            "Spot avg": money_col("Spot avg"), "Diff vs C": st.column_config.NumberColumn(f"Diff vs “C” {unit()}", format="$%+.2f"),
            "Customs": money_col("Customs unit value"),
            "7d %": st.column_config.NumberColumn(format="%+.1f%%"), "30d %": st.column_config.NumberColumn(format="%+.1f%%"),
            "YoY %": st.column_config.NumberColumn(format="%+.1f%%"),
            "Calendar": st.column_config.TextColumn("Harvest calendar", help="Capital = main harvest month, lower-case = fly crop"),
            "Trends": st.column_config.LinkColumn("Trends", display_text="chart →"),
        },
    )
    st.caption("Customs unit values are a broad market signal across all grades, not a specialty price. "
               "“small packs” = only 20–22 lb boxes offered (small-lot premium).")


def page_offers() -> None:
    v = data_version()
    rules = costing.load_rules()
    st.title("Spot offers")
    df = q_offers(v)
    if df.empty:
        st.info("No current importer offers yet. They load in the background on first start, or upload a spot sheet on Data Sources.")
        return
    f = st.columns([2, 2, 2, 1.3, 1.3])
    countries = f[0].multiselect("Country", sorted(df["Country"].dropna().unique()))
    processes = f[1].multiselect("Process", sorted(df["Process"].dropna().unique()))
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
    d["Roasted"] = d["landed"] / (1 - shrink)
    for col in ("spot", "freight", "landed", "Roasted"):
        d[col] = d[col] * k()
    d = d.sort_values("landed")

    c1, c2, c3 = st.columns(3)
    c1.metric("Lots", len(d))
    c2.metric(f"Median landed {unit()}", "—" if d.empty else f"${d['landed'].median():,.2f}")
    c3.metric(f"Cheapest roasted {unit()}", "—" if d.empty else f"${d['Roasted'].min():,.2f}")

    st.dataframe(
        d[["Importer", "Country", "Lot", "Process", "Score", "Bag kg", "Warehouse", "spot", "freight", "landed", "Roasted",
           "Bags avail.", "Link"]],
        hide_index=True, width="stretch", height=560,
        column_config={
            "Lot": st.column_config.TextColumn(width="large"),
            "spot": money_col("Spot"), "freight": money_col("Freight"), "landed": money_col("Landed ATL"),
            "Roasted": money_col("Roasted"), "Score": st.column_config.NumberColumn(format="%.1f"),
            "Bag kg": st.column_config.NumberColumn(format="%.1f"),
            "Link": st.column_config.LinkColumn("Link", display_text="open"),
        },
    )
    st.download_button("Download CSV", d.drop(columns=["iso"]).to_csv(index=False).encode(), "originpulse_offers.csv", "text/csv")
    st.caption(f"Landed = spot + LTL freight to Atlanta + ${rules['warehouse_fee_usd_lb']:.2f}/lb warehouse & financing. "
               f"Roasted = landed ÷ (1 − shrinkage).")


def page_trends() -> None:
    v = data_version()
    mat = q_matrix(v)
    names = {c["iso_code"]: c["name"] for c in mat["countries"]}
    priced = sorted(names, key=lambda i: (mat_lookup(mat, i)["spot_avg_usd_lb"] is None, names[i]))
    qp = st.query_params.get("country", "")
    default = qp.upper() if qp.upper() in names else (priced[0] if priced else "ETH")
    st.title("Trends")
    a, b, c = st.columns([2, 2, 2])
    iso = a.selectbox("Country", list(names), index=list(names).index(default), format_func=names.get)
    st.query_params["country"] = iso
    range_ = c.segmented_control("Range", ["30d", "90d", "1y", "3y"], default="1y") or "1y"
    h0 = q_history(v, iso, None, range_)
    process = b.selectbox("Process", ["All processes", *h0["processes"]])
    h = h0 if process == "All processes" else q_history(v, iso, process, range_)

    pts = pd.DataFrame(h["points"])
    if pts.empty:
        st.info("No price history yet.")
        return
    pts["date"] = pd.to_datetime(pts["date"])
    st.subheader(f"{names[iso]} spot average vs ICE “C”")
    fig = go.Figure()
    fig.add_scatter(x=pts["date"], y=pts["ice_c_usd_lb"] * k(), name="ICE “C”", line=dict(color=SERIES[1], width=2),
                    hovertemplate="%{y:$.4f}")
    sparse = pts["spot_avg_usd_lb"].notna().sum() < 10
    fig.add_scatter(x=pts["date"], y=pts["spot_avg_usd_lb"] * k(), name=f"{names[iso]} spot avg", connectgaps=True,
                    mode="lines+markers" if sparse else "lines", marker=dict(size=8),
                    line=dict(color=SERIES[0], width=2), hovertemplate="%{y:$.2f}")
    st.plotly_chart(line_layout(fig), width="stretch")
    basis = pts["spot_basis"].dropna()
    if not basis.empty and basis.iloc[-1] == "small packs":
        st.caption("Spot average is from small packs (20–22 lb boxes), which carry a small-lot premium.")

    d = pts[pts["diff_usd_lb"].notna()]
    st.subheader("Differential over “C”")
    trend = h["spread_trend"]
    if trend:
        st.caption(f"Spread is **{trend['direction']}** ({signed(trend['usd_lb_per_30d'])} per 30 days).")
    if d.empty:
        st.caption("Needs live spot offers for this origin.")
    else:
        fig = go.Figure(go.Scatter(x=d["date"], y=d["diff_usd_lb"] * k(), name="Differential",
                                   line=dict(color=SERIES[0], width=2), hovertemplate="%{y:$+.2f}"))
        fig.add_hline(y=0, line_color="rgba(128,128,128,0.6)", line_width=1)
        st.plotly_chart(line_layout(fig, 240, "$+.2f"), width="stretch")

    tr = pd.DataFrame(h["trade"])
    st.subheader("Customs import unit values")
    if tr.empty:
        st.caption("No customs data for this origin in this range yet.")
    else:
        tm = tr[tr["freq"] == "M"].copy()
        if not tm.empty:
            tm["period"] = pd.to_datetime(tm["period"])
            tm["price"] = tm["unit_usd_lb"] * k()
            fig = px.line(tm, x="period", y="price", color="reporter", markers=True, color_discrete_sequence=SERIES,
                          labels={"reporter": "Imports into"})
            fig.update_traces(line_width=2, marker_size=8, hovertemplate="%{y:$.2f}")
            st.plotly_chart(line_layout(fig, 260), width="stretch")
        ta = tr[tr["freq"] == "A"]
        if not ta.empty:
            st.dataframe(ta.assign(year=ta["period"].str[:4], price=ta["unit_usd_lb"] * k())[["year", "reporter", "price", "volume_kg"]],
                         hide_index=True, column_config={"price": money_col("Annual unit value"),
                                                         "volume_kg": st.column_config.NumberColumn("Volume kg", format="%,.0f")})
    with st.expander("Table view"):
        st.dataframe(pts, hide_index=True, width="stretch")


def mat_lookup(mat: dict, iso: str) -> dict:
    return next(c for c in mat["countries"] if c["iso_code"] == iso)


def page_calculator() -> None:
    rules = costing.load_rules()
    st.title("Landed cost calculator")
    st.caption(f"Destination: {rules['destination']}")
    warehouses = [w["name"] for t in rules["tiers"] for w in t["warehouses"]]

    a, b = st.columns([1, 1])
    with a:
        price_unit = st.radio("Price quoted per", ["lb", "kg", "bag"], horizontal=True)
        bag_kg = st.selectbox("Bag size (kg)", list(STANDARD_BAG_KG), index=1) if price_unit == "bag" else None
        price = st.number_input(f"Spot price ($ per {price_unit})", min_value=0.01,
                                value={"lb": 4.00, "kg": 8.82, "bag": 608.48}[price_unit], step=0.05)
        wh = st.selectbox("Warehouse", [*warehouses, "Other / unknown"])
    with b:
        match = costing.match_warehouse(None if wh == "Other / unknown" else wh, rules)
        freight = st.number_input("LTL freight to Atlanta ($/lb)", 0.0, 2.0, float(match.freight_usd_lb), 0.01,
                                  help=f"{match.tier_label} tier")
        fee = st.number_input("Warehouse & financing fee ($/lb)", 0.0, 1.0, float(rules["warehouse_fee_usd_lb"]), 0.01)
        shrink = st.slider("Roast shrinkage", 0.10, 0.25, float(rules["default_shrinkage"]), 0.005, format="%.3f")
        bags = st.number_input("Bags in order (optional)", 0, 1000, 10)

    spot_lb = price / (bag_kg * LB_PER_KG) if price_unit == "bag" else (price / LB_PER_KG if price_unit == "kg" else price)
    bd, _ = costing.cost_breakdown(spot_lb, None if wh == "Other / unknown" else wh, rules=rules,
                                   freight_override=freight, fee_override=fee, shrinkage=shrink)
    st.divider()
    m = st.columns(4)
    m[0].metric(f"Spot {unit()}", money(bd.spot_usd_lb))
    m[1].metric(f"+ Freight & fees {unit()}", money(bd.freight_usd_lb + bd.fee_usd_lb))
    m[2].metric(f"Landed green {unit()}", money(bd.landed_usd_lb))
    m[3].metric(f"True roasted {unit()}", money(bd.roasted_usd_lb))
    if bags and (bag_kg or price_unit != "bag"):
        kg_each = bag_kg or 69.0
        lbs = bags * kg_each * LB_PER_KG
        st.caption(f"Order: {bags} × {kg_each:g} kg = {lbs:,.0f} lb green → {lbs * (1 - shrink):,.0f} lb roasted · "
                   f"landed total **${lbs * bd.landed_usd_lb:,.0f}** (green ${lbs * spot_lb:,.0f}, "
                   f"freight ${lbs * freight:,.0f}, fees ${lbs * fee:,.0f})")

    st.subheader("Same lot from each warehouse")
    comp = []
    for t in rules["tiers"]:
        for w in t["warehouses"]:
            b2, _ = costing.cost_breakdown(spot_lb, w["name"], rules=rules, fee_override=fee, shrinkage=shrink)
            comp.append({"Warehouse": w["name"], "Tier": t["label"], "Freight": b2.freight_usd_lb * k(),
                         "Landed": b2.landed_usd_lb * k(), "Roasted": b2.roasted_usd_lb * k()})
    cdf = pd.DataFrame(comp).sort_values("Landed")
    fig = px.bar(cdf, x="Landed", y="Warehouse", color="Tier", orientation="h", color_discrete_sequence=SERIES,
                 hover_data={"Freight": ":$.2f", "Roasted": ":$.2f", "Landed": ":$.2f"})
    fig.update_layout(height=40 + 32 * len(cdf), margin=dict(l=8, r=8, t=8, b=8), bargap=0.25,
                      legend=dict(orientation="h", y=1.1, title=None), xaxis_tickformat="$.2f",
                      yaxis=dict(categoryorder="total descending", title=None), xaxis_title=f"Landed {unit()}")
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
        e1, e2, e3 = st.columns(3)
        new_fee = e1.number_input("Default fee $/lb", 0.0, 1.0, float(rules["warehouse_fee_usd_lb"]), 0.01, key="r_fee")
        new_unknown = e2.number_input("Unknown warehouse freight $/lb", 0.0, 2.0,
                                      float(rules["unknown_warehouse_freight_usd_lb"]), 0.01, key="r_unk")
        new_shrink = e3.number_input("Default shrinkage", 0.0, 0.5, float(rules["default_shrinkage"]), 0.005, key="r_sh")
        if st.button("Save freight rules", type="primary"):
            new = {**rules, "warehouse_fee_usd_lb": new_fee, "unknown_warehouse_freight_usd_lb": new_unknown,
                   "default_shrinkage": new_shrink, "tiers": [dict(t) for t in rules["tiers"]]}
            for t, (_, r) in zip(new["tiers"], edited.iterrows()):
                t.update(low=float(r["Low"]), default=float(r["Default"]), high=float(r["High"]))
            try:
                costing.save_rules(new)
                st.success("Saved. Existing offers keep their stored freight until the next scrape.")
                st.rerun()
            except (ValueError, KeyError) as e:
                st.error(str(e))


def page_alerts() -> None:
    v = data_version()
    st.title("Alerts")
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
            badge = "" if x["is_read"] else "🔵 "
            l.markdown(f"{badge}**{TITLES.get(x['alert_type'], x['alert_type'])}** · {x['created_at']:%b %d, %H:%M} UTC  \n{x['message']}")
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
    st.title("Data sources")
    busy = running_jobs()
    if busy:
        st.info(f"Running now: {', '.join(busy)}. This page updates when they finish.", icon="⏳")

    runs, files = q_runs(v)
    with session_scope() as s:
        counts = {
            "Benchmark days": s.exec(select(func.count(MarketBenchmark.id))).one(),
            "Customs rows": s.exec(select(func.count(TradeValuation.id))).one(),
            "Current offers": len(analytics.current_offers(s)),
            "Alerts": s.exec(select(func.count(Alert.id))).one(),
        }
    cols = st.columns(4)
    for col, (label, n) in zip(cols, counts.items()):
        col.metric(label, f"{n:,}")

    st.subheader("Run a pipeline")
    st.caption("Benchmarks, scrapers and alerts take seconds. Customs data is slow (rate-limited API) and runs in the background.")
    if admin_gate("Running pipelines"):
        bc = st.columns(5)
        for col, (job, label) in zip(bc, [("benchmarks", "ICE & FX"), ("scrapers", "Importer catalogs"),
                                           ("dropzone", "Spot-sheet folder"), ("alerts", "Alert engine"), ("trade", "Customs data")]):
            if col.button(label, width="stretch", disabled=job in busy):
                if job == "trade":
                    _bg(lambda: (jobs.job_trade(), jobs.job_alerts(deliver=False)), "trade")
                    st.toast("Customs refresh started in the background.")
                else:
                    with st.spinner(f"Running {label}…"):
                        res = jobs.job_alerts(deliver=False) if job == "alerts" else jobs.JOBS[job]()
                    st.toast(f"{label}: {res.get('status', 'done') if isinstance(res, dict) else 'done'}")
                st.rerun()

    st.subheader("Upload spot sheets")
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

    st.subheader("Recent runs")
    if runs:
        rdf = pd.DataFrame(runs)[["job", "started_at", "finished_at", "status", "rows", "message"]]
        st.dataframe(rdf, hide_index=True, width="stretch", height=300,
                     column_config={"message": st.column_config.TextColumn(width="large")})
    if files:
        st.subheader("Ingested files")
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
        st.radio("Units", ["$/lb", "$/kg"], horizontal=True, key="unit")
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


def main() -> None:
    with st.spinner("First start: loading 5 years of ICE “C” and FX prices… (about 30 seconds)"):
        bootstrap()
    refresh_if_stale()
    sidebar()
    pages = [
        st.Page(page_overview, title="Overview", icon="🌍", url_path="overview", default=True),
        st.Page(page_matrix, title="Origin matrix", icon="🗺️", url_path="origins"),
        st.Page(page_offers, title="Spot offers", icon="📦", url_path="offers"),
        st.Page(page_trends, title="Trends", icon="📈", url_path="trends"),
        st.Page(page_calculator, title="Landed cost", icon="🧮", url_path="calculator"),
        st.Page(page_alerts, title="Alerts", icon="🔔", url_path="alerts"),
        st.Page(page_data, title="Data sources", icon="🗄️", url_path="data"),
    ]
    st.navigation(pages).run()


main()
