"""Capacity early-warning dashboard (AO-02). Read-only: never changes facility controls."""
from html import escape

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import ui_fx
import ui_story
from pipeline import audit, backtest, clean, engine, loader
from pipeline.exhaustion import first_crossing
from pipeline.forecast import smooth

st.set_page_config(page_title="ForeSight · Capacity Early Warning", page_icon=":material/monitoring:",
                   layout="wide", initial_sidebar_state="expanded")

# One palette for every Plotly figure; matches .streamlit/config.toml
UI = {"bg": "#050505", "panel": "#0d0d0d", "grid": "#1c1c1c", "line": "#2e2e2e",
      "text": "#d4d4d4", "muted": "#a3a3a3", "faint": "#6b6b6b", "strong": "#f5f5f5",
      "accent": "#f2f2f2", "font": "Inter, Segoe UI, sans-serif",
      "mono": "JetBrains Mono, ui-monospace, monospace"}
# Translucent tints keep the theme's own text colour readable on the dark panels
SEV_COLOR = {"CRITICAL": "rgba(244, 63, 94, 0.16)", "WARNING": "rgba(245, 158, 11, 0.14)",
             "PLAN": "rgba(59, 130, 246, 0.14)", "HOLD": "rgba(100, 116, 139, 0.16)", "OK": None}
SEV_SOLID = {"CRITICAL": "#f43f5e", "WARNING": "#f59e0b", "PLAN": "#3b82f6",
             "HOLD": "#64748b", "OK": "#10b981"}
SEV_ICON = {"CRITICAL": ":material/error:", "WARNING": ":material/warning:",
            "PLAN": ":material/event_upcoming:", "HOLD": ":material/hourglass_top:",
            "OK": ":material/check_circle:"}
SEV_BADGE = {"CRITICAL": "red", "WARNING": "orange", "PLAN": "blue", "HOLD": "gray",
             "OK": "green"}
SEV_ORDER = {"CRITICAL": 0, "WARNING": 1, "PLAN": 2, "HOLD": 3, "OK": 4}
PALETTE = ["#38bdf8", "#2dd4bf", "#a78bfa", "#f472b6", "#a3e635", "#94a3b8", "#60a5fa"]
GAUGE_GREEN, GAUGE_YELLOW, GAUGE_RED = ("rgba(16, 185, 129, 0.75)", "rgba(234, 179, 8, 0.75)",
                                        "rgba(244, 63, 94, 0.8)")
ATTN_YELLOW, ATTN_RED = "#facc15", "#f43f5e"       # attention lines: safe limit / full
TOTAL_COLOR = "#fb923c"                            # rack total: outside the tenant palette
# Line colours avoid yellow/red so the attention lines always stand out
RES_COLOR = {"storage_tb": "#38bdf8", "network_gbps": "#2dd4bf", "vm_count": "#a78bfa",
             "power_kw": "#f472b6", "temp_c": "#94a3b8", "licences": "#a3e635",
             "cpu_pct": "#38bdf8", "mem_pct": "#a78bfa", "disk_io_pct": "#2dd4bf"}
TENANT_COLOR_LIST = ["#38bdf8", "#2dd4bf", "#a78bfa", "#f472b6", "#a3e635", "#94a3b8"]
WORKLOADS = {"Compute": "vm_count", "Storage": "storage_tb", "Network": "network_gbps",
             "Licences": "licences",
             "CPU": "cpu_pct", "Memory": "mem_pct", "Disk I/O": "disk_io_pct"}


def available_workloads(rows):
    """The WORKLOADS this data actually has - each data centre measures different things."""
    present = {r["resource"] for r in rows}
    return {wl: res for wl, res in WORKLOADS.items() if res in present}
ALERTING = ("CRITICAL", "WARNING", "PLAN")
HISTORY_DAYS = 180

# ---------------------------------------------------------------- data-centre record
RECORDS = config.records()


def new_record():
    """Rack, workload and tenant picks belong to the previous record - start fresh."""
    for k in list(st.session_state):
        if k != "record":
            del st.session_state[k]


with st.sidebar:
    record = st.selectbox(
        "Data centre", list(RECORDS), key="record", on_change=new_record,
        index=next((i for i, p in enumerate(RECORDS.values()) if config.is_active(p)), 0),
        help="Each record is a separate data centre with its own audit log.")
if not config.is_active(RECORDS[record]):     # keeps a test's redirected audit dir intact
    config.use_record(RECORDS[record])

# Daily records show durations in weeks. An hourly record stores each real hour as one model
# day counted from its epoch, so it shows durations in hours and times as "Day N HH:00".
HOURLY = config.RECORD["step"] == "hour"
EPOCH = pd.Timestamp(config.RECORD["epoch"])
UNIT_DAYS, U, U_LONG = (1, "h", "hours") if HOURLY else (7, "wks", "weeks")
STEP, RUNS = ("hour", "hourly") if HOURLY else ("day", "daily")
if HOURLY:
    st.sidebar.caption("1 model step = 1 real hour. Times read *Day N HH:00* of the trace; "
                       "Validation and Audit tables keep model dates (2000-01-01 = Day 1 00:00).")


# ---------------------------------------------------------------- cached data access
@st.cache_data(show_spinner="Loading telemetry…")
def load(data_dir):
    """data_dir is only the cache key: one cached load per data-centre record."""
    tel, lim, ev, source = loader.load()
    return tel, lim, ev, source, loader.data_hash(tel)


@st.cache_data(show_spinner=False)
def series_for(viewer, data_hash):
    """TENANT ISOLATION: a tenant's rows are filtered out BEFORE any computation, and a
    tenant view never gets rack totals (they would reveal other tenants' usage)."""
    tel = load(str(config.DATA_DIR))[0]
    if viewer != "Operator":
        tel = tel[tel["tenant"] == viewer]
        return {}, clean.build_series(tel)[1]
    return clean.build_series(tel)


@st.cache_data(show_spinner="Forecasting…")
def operator_run(data_hash, cutoff):
    rack_series, tenant_series = series_for("Operator", data_hash)
    return engine.run_operator(rack_series, tenant_series, load(str(config.DATA_DIR))[1], cutoff)  # audited once


@st.cache_data(show_spinner="Forecasting…")
def tenant_run(viewer, data_hash, cutoff):
    return engine.run_tenant(viewer, series_for(viewer, data_hash)[1], cutoff)


@st.cache_data(show_spinner="Forecasting tenants…")
def tenant_breakdown(data_hash, cutoff):
    """Operator only: forecast every tenant's own usage series (audited once)."""
    return engine.run_tenant_series(series_for("Operator", data_hash)[1], cutoff, "operator")


@st.cache_data(show_spinner="Back-testing every day of history (cached after first run)…")
def validation(data_hash):
    rack_series, _ = series_for("Operator", data_hash)
    _, lim, ev, _, _ = load(str(config.DATA_DIR))
    return backtest.run(rack_series, lim, ev)


# ---------------------------------------------------------------- helpers
def dur(days):
    """Model days -> the display unit U (weeks, or hours on an hourly record); None/NaN safe."""
    return None if days is None or days != days else round(days / UNIT_DAYS, 1)


def span(days):
    return f"{days // UNIT_DAYS} {U_LONG}"


def when(ts):
    if not HOURLY:
        return f"{ts:%d %b %Y}"
    h = (ts - EPOCH) // pd.Timedelta(days=1)
    return f"Day {h // 24 + 1} {h % 24:02d}:00"


def real_time(fig):
    """Hourly record: squeeze the date axis back to real time (1 model day -> 1 hour)."""
    if not HOURLY:
        return fig

    def f(x):
        return None if x is None else EPOCH + (pd.Timestamp(x) - EPOCH) / 24
    for t in fig.data:
        if t.x is not None:
            t.x = [f(x) for x in t.x]
    for sh in fig.layout.shapes:
        if (sh.xref or "x") == "x":
            sh.x0, sh.x1 = f(sh.x0), f(sh.x1)
    for an in fig.layout.annotations:
        if (an.xref or "x") == "x":
            an.x = f(an.x)
    if fig.layout.xaxis.range:
        fig.update_xaxes(range=[f(x) for x in fig.layout.xaxis.range])
    return fig.update_xaxes(tickformat="Day %-j<br>%H:%M", hoverformat="Day %-j %H:%M")


def safe_in(d, bold=False):
    """'safe limit in 3.4 wks', or 'past safe limit' when it is already crossed."""
    if d == 0:
        return "past safe limit"
    v = f"{fmt_eta(d)} {U}"
    return f"safe limit in **{v}**" if bold else f"safe limit in {v}"


def fmt_eta(d):
    return "—" if d is None else ("now" if d == 0 else f"{dur(d)}")


def eta_range(e):
    if e["likely"] is None and e["earliest"] is None:
        return "beyond horizon"
    latest = fmt_eta(e["latest"]) if e["latest"] is not None else f">{config.HORIZON_DAYS // UNIT_DAYS}"
    return f"{fmt_eta(e['earliest'])}–{latest} {U}"


def eta_title(r):
    e, name = r["eta"], f"{r['rack']} {r['resource']}"
    if e["likely"] == 0:
        return f"{name}: ALREADY past the safety margin"
    if e["likely"] is None:
        return f"{name}: no exhaustion expected within {span(config.HORIZON_DAYS)}"
    return f"{name}: likely in {dur(e['likely'])} {U_LONG} ({eta_range(e)})"


def styled(fig, height=320, legend=True):
    """Shared console look for every Plotly figure."""
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", height=height, showlegend=legend,
                      font=dict(family=UI["font"], size=11, color=UI["muted"]),
                      margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", y=1.08, x=1, xanchor="right",
                                  font=dict(color=UI["text"])),
                      hoverlabel=dict(bgcolor=UI["panel"], bordercolor=UI["line"],
                                      font=dict(family=UI["font"], color=UI["strong"], size=12)))
    fig.update_xaxes(gridcolor=UI["grid"], linecolor=UI["line"], zeroline=False,
                     spikecolor=UI["faint"], spikethickness=1, spikedash="dot")
    fig.update_yaxes(gridcolor=UI["grid"], linecolor=UI["line"], zeroline=False)
    return fig


def plot(fig):
    st.plotly_chart(fig, theme=None, config={"displaylogo": False})


_panel_ids = iter(range(10_000))      # unique keys per run so panels can share CSS styling
_section_no = iter(range(1, 100))     # visible section numbers, restart on every tab


def panel(title, icon=None, sub=None):
    """A bordered HUD card: numbered mono section header, optional one-line subtitle.
    icon is kept for call-site readability; the header style replaces it visually."""
    box = st.container(border=True, key=f"panel_{next(_panel_ids)}")
    sub_html = f'<p class="ph-sub">{escape(sub)}</p>' if sub else ""
    box.html(f'<div class="ph"><span class="ph-idx">{next(_section_no):02d}</span>'
             f'<span class="ph-title">{escape(title)}</span><span class="ph-rule"></span></div>'
             f"{sub_html}")
    return box


def new_tab():
    """Restart section numbering at 01 for each tab."""
    global _section_no
    _section_no = iter(range(1, 100))


def kpi(label_, value, tone="neutral", **kw):
    """A metric card with a coloured accent edge (tone = severity name or 'neutral')."""
    with st.container(border=True, key=f"kpi_{tone.lower()}_{next(_panel_ids)}"):
        st.metric(label_, value, **kw)


def sev_badge(status):
    return f":{SEV_BADGE[status]}-badge[{SEV_ICON[status]} {status}]"


def label(r):
    return f"{r['rack'].replace('rack-', 'R')} {r['resource'].split('_')[0]}"


def headroom_pct(r):
    """Distance to the safe limit (capacity - margin) as % of capacity."""
    return 100 * (r["threshold"] - r["fc"]["last_level"]) / r["capacity"]


def gauge(r):
    """Half-dial gauge of % capacity used, big number and ETA underneath (DCIM style)."""
    used = 100 * r["fc"]["last_level"] / r["capacity"]
    safe = 100 * r["threshold"] / r["capacity"]
    e = r["eta"]
    sub = ("past safe limit" if e["likely"] == 0 else
           f"safe limit in {dur(e['likely'])} {U} · {r['confidence']['score']}% conf"
           if e["likely"] and r["confidence"] else
           f"safe limit in {dur(e['likely'])} {U}" if e["likely"] else
           "no exhaustion forecast")
    fig = go.Figure(go.Indicator(
        mode="gauge", value=used, domain=dict(x=[0.05, 0.95], y=[0.3, 1]),
        gauge=dict(axis=dict(range=[0, 100], dtick=25, tickcolor=UI["faint"], ticksuffix="%",
                             tickfont=dict(size=10, color=UI["muted"])),
                   bar=dict(color=UI["strong"], thickness=0.12), bgcolor="rgba(0,0,0,0)",
                   borderwidth=0,
                   steps=[dict(range=[0, safe - 20], color=GAUGE_GREEN),
                          dict(range=[safe - 20, safe], color=GAUGE_YELLOW),
                          dict(range=[safe, 100], color=GAUGE_RED)],
                   threshold=dict(line=dict(color=UI["accent"], width=3), value=safe))))
    fig.add_annotation(x=0.5, y=0.30, xref="paper", yref="paper", showarrow=False,
                       text=f"<b>{used:.0f}%</b>",
                       font=dict(family="JetBrains Mono, monospace", size=32, color=UI["strong"]))
    fig.add_annotation(x=0.5, y=0.02, xref="paper", yref="paper", showarrow=False,
                       text=f"USED<br>{sub}", font=dict(size=11, color=UI["muted"]))
    return styled(fig, height=260, legend=False).update_layout(margin=dict(t=10, b=10, l=30, r=30))


def conf_text(conf):
    return f"{conf['score']}% confidence ({conf['label']})" if conf else "confidence n/a"


def add_attention(fig, r, scale, name, i):
    """Yellow (safe limit) and red (full capacity) lines rising from the x-axis to the
    point where the forecast crosses - only drawn for resources that are alerting."""
    fc, e = r["fc"], r["eta"]
    marks = []
    if e["likely"] is not None:
        marks.append((e["likely"], r["threshold"], ATTN_YELLOW,
                      f"{name}: {safe_in(e['likely'])} · "
                      f"{conf_text(r['confidence'])}"))
    full = first_crossing(fc["p50"], r["capacity"])
    if full is not None:
        marks.append((full, r["capacity"], ATTN_RED, f"{name}: full in {dur(full)} {U}"))
    for j, (days, level, color, text) in enumerate(marks):
        d, y = fc["cutoff"] + pd.Timedelta(days=days), level * scale
        fig.add_shape(type="line", x0=d, x1=d, y0=0, y1=y, line=dict(color=color, width=2.5))
        fig.add_trace(go.Scatter(x=[d], y=[y], mode="markers", showlegend=False,
                                 marker=dict(color=color, size=10, line=dict(color=UI["bg"],
                                                                             width=2)),
                                 hovertemplate=f"{text}<extra></extra>"))
        fig.add_annotation(x=d, y=y, text=text, showarrow=False, xanchor="right",
                           yshift=14 + 18 * (2 * i + j), xshift=-6,
                           font=dict(color=color, size=11), bgcolor="rgba(10,10,10,0.9)",
                           bordercolor=color, borderwidth=1, borderpad=3)


def trend_chart(items, y_title, cutoff, attention=(), refs=(), height=440):
    """Multi-line chart: solid = actual (7-day median), dashed = P50 forecast.

    items: dicts with name, raw, fc, color, optional scale/width.
    attention: (result, scale, name) for alerting series -> yellow/red markers.
    refs: (y, label, color) horizontal reference lines.
    """
    fig = go.Figure()
    start = cutoff - pd.Timedelta(days=HISTORY_DAYS)
    for it in items:
        k, w = it.get("scale", 1.0), it.get("width", 2)
        sm = smooth(it["raw"]).loc[start:] * k
        fig.add_trace(go.Scatter(x=sm.index, y=sm.values, name=it["name"],
                                 legendgroup=it["name"], line=dict(color=it["color"], width=w),
                                 hovertemplate="%{y:.1f}<extra>" + it["name"] + "</extra>"))
        fc = it["fc"]
        if fc is not None:
            band = np.c_[np.r_[fc["last_level"], fc["p10"]], np.r_[fc["last_level"], fc["p90"]]]
            fig.add_trace(go.Scatter(
                x=[fc["cutoff"], *fc["dates"]], y=np.r_[fc["last_level"], fc["p50"]] * k,
                name=it["name"], legendgroup=it["name"], showlegend=False,
                line=dict(color=it["color"], width=w, dash="dash"), customdata=band * k,
                hovertemplate=("%{y:.1f} (P10–P90 %{customdata[0]:.1f}–%{customdata[1]:.1f})"
                               "<extra>" + it["name"] + " forecast</extra>")))
    for y, text, color in refs:
        fig.add_hline(y=y, line=dict(color=color, dash="dot", width=1), annotation_text=text,
                      annotation_font_color=color, annotation_position="top left")
    fig.add_vrect(x0=cutoff, x1=cutoff + pd.Timedelta(days=config.HORIZON_DAYS),
                  fillcolor="rgba(255, 255, 255, 0.025)", line_width=0, layer="below")
    fig.add_shape(type="line", x0=cutoff, x1=cutoff, yref="paper", y0=0, y1=1,
                  line=dict(color=UI["muted"], dash="dot", width=1))
    fig.add_annotation(x=cutoff, yref="paper", y=1.03, text="TODAY", showarrow=False,
                       font=dict(color=UI["muted"], size=10))
    for i, (r, k, name) in enumerate(attention):
        add_attention(fig, r, k, name, i)
    for name, dash in (("actual", "solid"), ("forecast", "dash")):   # line-style legend
        fig.add_trace(go.Scatter(x=[None], y=[None], name=name, mode="lines",
                                 line=dict(color=UI["muted"], dash=dash)))
    styled(fig, height=height)
    fig.update_layout(hovermode="x unified", yaxis_title=y_title,
                      legend=dict(orientation="h", y=-0.12, x=0, xanchor="left"))
    fig.update_xaxes(range=[start, cutoff + pd.Timedelta(days=config.HORIZON_DAYS)])
    fig.update_yaxes(rangemode="tozero")
    return real_time(fig)


def status_help(status, rows):
    """Hover text for a status card: which series, when, how sure, and what to do."""
    hits = [r for r in rows if r["status"] == status]
    if not hits:
        return f"No {status} series right now."
    lines = [f"- **{r['rack']} · {r['resource']}** — {safe_in(r['eta']['likely'])} "
             f"({eta_range(r['eta'])}), "
             f"{conf_text(r['confidence'])}; {r['driver']}" for r in hits]
    return "\n".join(lines) + f"\n\n**Action:** {config.RECOMMENDATION[status]}"


def alert_line(r):
    e = r["eta"]
    return (f"{sev_badge(r['status'])} **{r['rack']} · {r['resource']}** — "
            f"{safe_in(e['likely'], bold=True)} ({eta_range(e)}) · "
            f"{conf_text(r['confidence'])} · driver: {r['driver']}")


def chart(raw, fc, title, unit, cap=None, thr=None, events=None, height=480):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=raw.index, y=raw.values, name=RUNS.capitalize(), mode="lines",
                             line=dict(color="rgba(148, 163, 184, 0.45)", width=1)))
    fig.add_trace(go.Scatter(x=raw.index, y=smooth(raw).values, name=f"7-{STEP} median",
                             line=dict(color="#38bdf8", width=2.5)))
    if fc is not None:
        x = list(fc["dates"]) + list(fc["dates"][::-1])
        fig.add_trace(go.Scatter(x=x, y=list(fc["p90"]) + list(fc["p10"][::-1]), fill="toself",
                                 fillcolor="rgba(56, 189, 248, 0.16)", line=dict(width=0),
                                 name="P10–P90", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=fc["dates"], y=fc["p50"], name="P50 forecast",
                                 line=dict(color="#38bdf8", dash="dot", width=2)))
    if cap is not None:
        fig.add_hline(y=cap, line=dict(color=ATTN_RED, dash="dash"), annotation_text="capacity",
                      annotation_font_color=ATTN_RED)
        fig.add_hline(y=thr, line=dict(color=SEV_SOLID["WARNING"], dash="dash"),
                      annotation_text="capacity − safety margin",
                      annotation_font_color=SEV_SOLID["WARNING"])
    marks = [(raw.index[-1], "TODAY", UI["muted"])]
    if fc is not None:
        marks += [(c, "change point", "#a78bfa") for c in fc["change_points"]]
    for i, (d, text, color) in enumerate(marks):     # stagger labels so close ones don't overlap
        fig.add_shape(type="line", x0=d, x1=d, yref="paper", y0=0, y1=1,
                      line=dict(color=color, dash="dot", width=1))
        fig.add_annotation(x=d, yref="paper", y=1.03 + 0.05 * (i % 2), text=text,
                           showarrow=False, font=dict(color=color, size=10))
    if events is not None and len(events):
        y = raw.reindex(events.timestamp.dt.normalize()).values
        fig.add_trace(go.Scatter(x=events.timestamp, y=y, mode="markers", name="Past events",
                                 marker=dict(symbol="x", size=12, color=ATTN_RED),
                                 text=events.event_type + ": " + events.description,
                                 hoverinfo="text+x"))
    styled(fig, height=height)
    fig.update_layout(title=dict(text=title, font=dict(size=13, color=UI["strong"])),
                      yaxis_title=unit,
                      hovermode="x unified", margin=dict(t=60),
                      legend=dict(orientation="h", y=-0.12, x=0, xanchor="left"))
    return real_time(fig)


def explanation(r):
    e, res, rack = r["eta"], r["resource"], r["rack"]
    if e["likely"] == 0:
        head = f"**{res} on {rack} is already past its safety margin.**"
    elif e["likely"] is not None:
        head = (f"**{res} on {rack} will likely run out in ~{dur(e['likely'])} {U_LONG}** "
                f"({eta_range(e)}).")
    elif e["earliest"] is not None:
        head = (f"{res} on {rack} is not expected to run out within "
                f"{span(config.HORIZON_DAYS)}; worst case {dur(e['earliest'])} {U_LONG}.")
    else:
        head = f"{res} on {rack} has no forecast exhaustion within {span(config.HORIZON_DAYS)}."
    top, fc = r["top"], r["fc"]
    if top is not None:
        since = ""
        if fc["change_points"] and fc["fit_start"] == fc["change_points"][-1]:
            since = f" since {when(fc['fit_start'])}, when the trend changed"
        why = f" {top['share_pct']:.0f}% of the growth comes from **{top['tenant']}**{since}."
    elif r["shares"] == [] and "rack-level" in r["driver"]:
        why = " This is a rack-level metric, so it cannot be split by tenant."
    else:
        why = " No tenant is growing significantly."
    note = ""
    if r["status"] == "HOLD":
        note = (f" A crossing is forecast but not yet confirmed (needs {config.SUSTAIN_RUNS} "
                f"consecutive {RUNS} runs and {span(config.MIN_FIT_DAYS)} of data after any "
                "change point).")
    return f"{head}{why}{note}\n\n**Recommended:** {r['recommendation']}"


# ---------------------------------------------------------------- page
tel, lim, ev, source, h = load(str(config.DATA_DIR))
tenants = sorted(tel["tenant"].dropna().unique())
TENANT_COLOR = {t: TENANT_COLOR_LIST[i % len(TENANT_COLOR_LIST)] for i, t in enumerate(tenants)}
cutoff = tel["timestamp"].max().normalize()

ASSETS = config.ROOT / "assets"
st.html(ASSETS / "style.css")
st.logo(ASSETS / "logo.svg", icon_image=ASSETS / "mark.svg", size="large")
ui_fx.ambient()

with st.sidebar:
    viewer = st.selectbox("View as", ["Operator"] + tenants,
                          help="Simulates login. A tenant sees only their own usage.")
    st.html(f'''<dl class="side-kv"><dt>Source</dt><dd>{escape(source)}</dd>
<dt>Data as of</dt><dd>{when(cutoff)}</dd>
<dt>Horizon</dt><dd>{config.HORIZON_DAYS // UNIT_DAYS} {U}</dd>
<dt>Confirm</dt><dd>{config.SUSTAIN_RUNS} {RUNS} runs</dd></dl>''')
    st.html('<div class="side-foot"><span class="led"></span>Read-only · no facility '
            'control</div>')

results = operator_run(h, cutoff) if viewer == "Operator" else tenant_run(viewer, h, cutoff)


def story_payload(rows):
    """Operator-only data for the scroll story: each rack's combined resource chart and the
    tenant split of each workload. Never built for a tenant view."""
    rack_series, tenant_series = series_for("Operator", h)
    start = cutoff - pd.Timedelta(days=HISTORY_DAYS)
    end = cutoff + pd.Timedelta(days=config.HORIZON_DAYS)
    if HOURLY:      # one tick per real day = every 24 model days from the epoch
        ticks = [[(EPOCH - cutoff).days + 24 * k, f"Day {k + 1}"]
                 for k in range(-(-(start - EPOCH).days // 24), (end - EPOCH).days // 24 + 1)]
    else:
        ticks, m = [], (start + pd.offsets.MonthBegin(1))
        while m <= end:
            ticks.append([(m - cutoff).days, f"{m:%b}" + (f" {m:%y}" if m.month == 1 else "")])
            m += pd.offsets.MonthBegin(1)
    racks = []
    for rack in sorted({r["rack"] for r in rows}):
        mine = sorted((r for r in rows if r["rack"] == rack and r["fc"]),
                      key=lambda r: r["resource"])
        worst = min(mine, key=lambda r: (SEV_ORDER[r["status"]],
                                         -r["fc"]["last_level"] / r["capacity"]))
        series, ymax = [], 100.0
        for r in mine:
            k, fc, e = 100 / r["capacity"], r["fc"], r["eta"]
            # dropna: real data has gaps, and NaN is not valid JSON for the story component
            sm = smooth(rack_series[(rack, r["resource"])]).loc[start:].dropna() * k
            actual = [[(d - cutoff).days, round(float(v), 2)] for d, v in sm.iloc[::3].items()]
            fdays = [(d - cutoff).days for d in fc["dates"]]
            forecast = [[0, round(fc["last_level"] * k, 2)]] + [
                [d, round(float(v) * k, 2)] for d, v in zip(fdays[2::3], fc["p50"][2::3])]
            marks = []
            if r["status"] in ALERTING:
                if e["likely"] is not None:
                    marks.append({"kind": "safe", "day": e["likely"],
                                  "level": round(r["threshold"] * k, 1),
                                  "text": f"{r['resource'].split('_')[0]} safe limit "
                                          f"{fmt_eta(e['likely'])}{U[0]}"})
                full = first_crossing(fc["p50"], r["capacity"])
                if full is not None:
                    marks.append({"kind": "full", "day": full, "level": 100,
                                  "text": f"full {dur(full)}{U[0]}"})
            ymax = max(ymax, *(v for _, v in actual), *(v for _, v in forecast))
            series.append({"name": r["resource"], "color": RES_COLOR.get(r["resource"], UI["text"]),
                           "actual": actual, "forecast": forecast, "marks": marks,
                           "alert": r["status"] in ALERTING})
        we = worst["eta"]
        headline = (f"{worst['resource']}: {safe_in(we['likely'])} "
                    f"({eta_range(we)}) · {conf_text(worst['confidence'])} · driver "
                    f"{worst['driver']}" if worst["status"] in ALERTING
                    else "All resources inside plan over the forecast horizon.")
        racks.append({"name": rack, "status": worst["status"], "headline": headline,
                      "used": round(worst["fc"]["last_level"] / worst["capacity"], 3),
                      "ymax": round(ymax, 1), "series": series})

    tenants = {}
    for r in rows:
        if r["resource"] not in WORKLOADS.values() or not r["fc"]:
            continue
        shares = [{"tenant": t, "pct": round(100 * float(smooth(ser).iloc[-1]) / r["capacity"], 2)}
                  for (rk, res, t), ser in tenant_series.items()
                  if rk == r["rack"] and res == r["resource"]]
        tenants.setdefault(r["rack"], {})[r["resource"]] = {
            "shares": sorted(shares, key=lambda x: -x["pct"]),
            "used": round(100 * r["fc"]["last_level"] / r["capacity"], 2),
            "safe": round(100 * r["threshold"] / r["capacity"], 2)}

    counts = {s_: sum(r["status"] == s_ for r in rows) for s_ in ALERTING}
    nearest = min((r["eta"]["likely"] for r in rows if r["eta"]["likely"] is not None),
                  default=None)
    hud = [["IST clock", "--:--:-- IST", ""],
           ["Data as of", when(cutoff), ""],
           ["Critical", str(counts["CRITICAL"]), "crit" if counts["CRITICAL"] else ""],
           ["Warning", str(counts["WARNING"]), "warn" if counts["WARNING"] else ""],
           ["Plan", str(counts["PLAN"]), "plan" if counts["PLAN"] else ""],
           ["Soonest ETA", f"{dur(nearest)} {U}" if nearest is not None else "—", ""]]
    return {"racks": racks, "tenants": tenants, "ticks": ticks, "hud": hud,
            "critical": counts["CRITICAL"],
            "workloads": [[wl, res] for wl, res in available_workloads(rows).items()],
            "tenant_colors": TENANT_COLOR, "smooth_win": f"7-{STEP}",
            "lede": f"{len(rows)} rack × resource series from {source} telemetry, forecast "
                    f"{span(config.HORIZON_DAYS)} ahead: which resource runs out, when, why, "
                    "and what to do."}


def readout(cells):
    return '<dl class="readout">' + "".join(
        f'<div><dt>{escape(k)}</dt><dd class="{c}">{v}</dd></div>' for k, v, c in cells) + "</dl>"


if viewer == "Operator":
    ui_story.story(story_payload(results))
else:
    with st.container(key="hero"):
        left, right = st.columns([6, 5], vertical_alignment="center", gap="large")
        with left:
            st.html('<p class="sysline"><span class="led"></span>AO-02<span class="sep">//</span>'
                    'Tenant view<span class="sep">//</span>read-only</p>')
            st.html('<p class="brand"><b>Fore<span>Sight</span></b></p>')
            st.markdown("# Seeing trouble before the capacity runs out")
            st.html(f'<p class="lede">Every rack × resource forecast {span(config.HORIZON_DAYS)} '
                    'ahead: which resource runs out, when, why, and what to do.</p>')
            st.html(readout([("IST clock", '<span class="hud-clock">--:--:-- IST</span>', "cy"),
                             ("Data as of", when(cutoff), ""),
                             ("Scope", escape(viewer).upper(), "cy"),
                             ("My series", str(len(results)), ""),
                             ("Visibility", "OWN WORKLOADS", ""),
                             ("Mode", "READ-ONLY", "ok")]))
        with right:   # decorative only: no rack, status or capacity data reaches a tenant browser
            ui_fx.server_hall([{"name": "", "status": "NONE"}] * 4, live=False)

if viewer == "Operator":
    tab1, tab2, tab3, tab4 = st.tabs([":material/dashboard: Overview",
                                      ":material/query_stats: Resource detail",
                                      ":material/fact_check: Validation",
                                      ":material/history: Audit log"])

    with tab1:
        new_tab()
        rows = sorted(results, key=lambda r: (SEV_ORDER[r["status"]],
                                              r["eta"]["likely"] if r["eta"]["likely"] is not None else 9e9))
        statuses = [r["status"] for r in rows]
        alerts_ = [r for r in rows if r["status"] in ALERTING]
        nearest = next((r for r in rows if r["eta"]["likely"] is not None), None)
        rack_series, _ = series_for("Operator", h)
        racks = sorted({r["rack"] for r in rows})
        worst_rack = rows[0]["rack"]

        with st.container(horizontal=True):
            for s_ in ["CRITICAL", "WARNING", "PLAN", "HOLD"]:
                kpi(f":{SEV_BADGE[s_]}[{SEV_ICON[s_]}] {s_}", statuses.count(s_), s_,
                    help=status_help(s_, rows))
            kpi(":primary[:material/sensors:] Monitored", len(rows))
            kpi(":primary[:material/timer:] Soonest ETA",
                f"{dur(nearest['eta']['likely'])} {U}" if nearest else "—",
                help=f"{nearest['rack']} · {nearest['resource']}" if nearest else None)

        # ---- hero: every resource on one rack, % of capacity, actual vs forecast
        with panel("Capacity outlook", ":material/timeline:",
                   "Every resource on one rack as % of capacity — actual (solid) and P50 "
                   "forecast (dashed)"):
            if st.session_state.get("hero_rack") not in racks:
                st.session_state["hero_rack"] = worst_rack
            rack = st.segmented_control("Rack", racks, key="hero_rack",
                                        label_visibility="collapsed") or worst_rack
            mine = [r for r in rows if r["rack"] == rack and r["fc"]]
            items = [{"name": r["resource"], "raw": rack_series[(rack, r["resource"])],
                      "fc": r["fc"], "color": RES_COLOR.get(r["resource"], UI["text"]),
                      "scale": 100 / r["capacity"]} for r in sorted(mine, key=lambda r: r["resource"])]
            attn = [(r, 100 / r["capacity"], r["resource"]) for r in mine if r["status"] in ALERTING]
            plot(trend_chart(items, "% of capacity", cutoff, attn,
                             refs=[(100, "capacity", UI["faint"])], height=460))
            st.caption("Yellow line: forecast reaches the safe limit (capacity − safety margin). "
                       "Red line: forecast reaches full capacity. Drawn only for sustained alerts.")
            for r in sorted((r for r in mine if r["status"] != "OK"),
                            key=lambda r: SEV_ORDER[r["status"]]):
                st.markdown(alert_line(r))

        # ---- same philosophy per tenant, per workload
        with panel("Tenant demand by workload", ":material/groups:",
                   "One line per tenant plus the rack total — actual (solid) and forecast "
                   "(dashed)"):
            c1, c2 = st.columns([1, 1])
            wls = available_workloads(rows)
            wl_default = "Storage" if "Storage" in wls else next(iter(wls))
            wl = c1.segmented_control("Workload", list(wls), default=wl_default,
                                      key="wl") or wl_default
            res = wls[wl]
            wl_racks = sorted({r["rack"] for r in rows if r["resource"] == res})
            default_rack = next((r["rack"] for r in rows if r["resource"] == res), wl_racks[0])
            t_rack = c2.segmented_control("Rack ", wl_racks, default=default_rack,
                                          key=f"wl_rack_{res}") or default_rack
            total = next(r for r in rows if r["rack"] == t_rack and r["resource"] == res)
            tfc = {x["tenant"]: x["fc"] for x in tenant_breakdown(h, cutoff)
                   if x["rack"] == t_rack and x["resource"] == res}
            tser = series_for("Operator", h)[1]
            items = [{"name": ten, "raw": tser[(t_rack, res, ten)], "fc": fc_,
                      "color": TENANT_COLOR[ten]} for ten, fc_ in sorted(tfc.items())]
            items.append({"name": f"{t_rack} total", "raw": rack_series[(t_rack, res)],
                          "fc": total["fc"], "color": TOTAL_COLOR, "width": 3})
            attn = [(total, 1.0, f"{t_rack} total")] if total["status"] in ALERTING else []
            plot(trend_chart(items, f"{wl} ({config.UNITS.get(res, '')})", cutoff, attn,
                             refs=[(total["capacity"], "capacity", UI["faint"]),
                                   (total["threshold"], "safe limit", ATTN_YELLOW)], height=440))
            if total["status"] != "OK":
                st.markdown(alert_line(total))

        left, g1, g2 = st.columns([2, 1, 1])
        with left, panel("Headroom to safe limit", ":material/bar_chart:",
                                         "% of capacity left before the safety margin, tightest first"):
            hr = sorted((r for r in rows if r["fc"]), key=headroom_pct)
            fig = go.Figure(go.Bar(
                x=[label(r) for r in hr], y=[headroom_pct(r) for r in hr],
                marker_color=[SEV_SOLID[r["status"]] for r in hr],
                text=[f"{headroom_pct(r):.0f}" for r in hr], textposition="outside",
                marker_cornerradius=4, textfont=dict(color=UI["text"]),
                hovertext=[f"{r['rack']} · {r['resource']}: {r['status']}" for r in hr]))
            plot(styled(fig, height=280, legend=False)
                 .update_yaxes(title="headroom %").update_xaxes(tickangle=-45))
        for col, r in zip((g1, g2), (alerts_ + rows)[:2]):
            with col, panel(f"{r['rack']} · {r['resource']}", ":material/speed:"):
                plot(gauge(r))

        with panel("Forecast exhaustion", ":material/table_chart:",
                   "Every rack and resource, most urgent first"):
            df = pd.DataFrame([{
                "Status": r["status"], "Rack": r["rack"],
                "Resource": r["resource"],
                f"Likely ETA ({U})": fmt_eta(r["eta"]["likely"]), "Range": eta_range(r["eta"]),
                "Confidence": (f"{r['confidence']['score']}% {r['confidence']['label']}"
                               if r["confidence"] else "—"),
                "Used now": f"{100 * r['fc']['last_level'] / r['capacity']:.0f}%" if r["fc"] else "—",
                "Top driver": r["driver"], "Recommended decision": r["recommendation"]}
                for r in rows])

            def tint(row):
                color = SEV_COLOR[statuses[row.name]]
                return [f"background-color: {color}" if color else ""] * len(row)

            styler = (df.style.apply(tint, axis=1)
                      .map(lambda v: f"color: {SEV_SOLID[v]}; font-weight: 700",
                           subset=["Status"]))
            st.dataframe(styler, hide_index=True,
                         row_height=34, height=34 * (len(df) + 1) + 3,
                         column_config={"Top driver": st.column_config.TextColumn(width="medium"),
                                        "Recommended decision":
                                            st.column_config.TextColumn(width="large")})

    with tab2:
        new_tab()
        # Rack x resource matrix: every cell is a button showing status, % used and ETA
        by_key = {(x["rack"], x["resource"]): x for x in results}
        racks = sorted({x["rack"] for x in results})
        resources = sorted({x["resource"] for x in results})
        if st.session_state.get("detail_sel") not in by_key:
            worst = min(results, key=lambda x: (SEV_ORDER[x["status"]],
                                                 x["eta"]["likely"] if x["eta"]["likely"] is not None else 9e9))
            st.session_state["detail_sel"] = (worst["rack"], worst["resource"])

        def select(key):
            st.session_state["detail_sel"] = key

        with panel("Pick a rack and resource", ":material/grid_view:",
                   f"Each cell: % of capacity used · likely {U_LONG} until the safe limit"):
            header = st.columns([1] + [2] * len(resources), vertical_alignment="center")
            for col, res_ in zip(header[1:], resources):
                col.markdown(f"**{res_}**")
            for rack_ in racks:
                cols = st.columns([1] + [2] * len(resources), vertical_alignment="center")
                cols[0].markdown(f"**{rack_}**")
                for col, res_ in zip(cols[1:], resources):
                    cell = by_key.get((rack_, res_))
                    if cell is None:
                        col.markdown("—")
                        continue
                    used = (f"{100 * cell['fc']['last_level'] / cell['capacity']:.0f}%"
                            if cell["fc"] else "—")
                    eta = (f"{fmt_eta(cell['eta']['likely'])} {U}"
                           if cell["eta"]["likely"] is not None else "OK")
                    col.button(f"{used} · {eta}", icon=SEV_ICON[cell["status"]],
                               key=f"cell_{rack_}_{res_}", width="stretch",
                               type="primary" if st.session_state["detail_sel"] == (rack_, res_)
                               else "secondary",
                               on_click=select, args=((rack_, res_),))
            st.caption("Icon = status. OK = no exhaustion within the horizon.")
        rack, res = st.session_state["detail_sel"]
        r = by_key[(rack, res)]
        raw = series_for("Operator", h)[0][(rack, res)]
        evs = ev[(ev.rack == rack) & (ev.resource == res)]
        with panel(f"{rack} · {res}", ":material/show_chart:",
                   f"Status: {r['status']}"):
            plot(chart(raw, r["fc"], eta_title(r), config.UNITS.get(res, ""),
                       r["capacity"], r["threshold"], evs))
        a, b = st.columns([3, 2])
        with a, panel("Why, and what to do", ":material/lightbulb:"):
            st.markdown(explanation(r))
            if r["fc"]:
                fc = r["fc"]
                st.caption(f"Fit window {when(fc['fit_start'])} → {when(fc['fit_end'])} "
                           f"({fc['n_fit']} {STEP}s) · slope {fc['slope']:+.3f} "
                           f"{config.UNITS.get(res, '')}/{STEP} · residual σ {fc['resid_std']:.3f}"
                           f" · input sha256 {fc['input_sha256'][:12]}…")
        with b, panel("Share of growth by tenant (%)", ":material/group:"):
            if r["shares"]:
                sh = pd.DataFrame(r["shares"])
                fig = go.Figure(go.Bar(x=sh.share_pct, y=sh.tenant, orientation="h",
                                       marker_color=PALETTE[:len(sh)],
                                       text=sh.share_pct.map("{:.0f}%".format),
                                       textposition="outside", marker_cornerradius=4,
                                       textfont=dict(color=UI["text"])))
                plot(styled(fig, height=220, legend=False)
                     .update_yaxes(autorange="reversed").update_xaxes(range=[0, 115]))
            else:
                st.caption(r["driver"])

    with tab3:
        new_tab()
        if not ev.event_type.isin({t.lower() for t in config.EXHAUSTION_EVENT_TYPES}).any():
            st.warning("No exhaustion events matched, so lead time cannot be measured. "
                       f"Event types in the log: {sorted(ev.event_type.unique())} — add the "
                       "right ones to EXHAUSTION_EVENT_TYPES in config.py.")
        v = validation(h)
        s = v["summary"]
        with st.container(horizontal=True):
            kpi(":primary[:material/task_alt:] Events caught",
                f"{s['events_detected']}/{s['events']}")
            kpi(":primary[:material/timer:] Mean lead time",
                f"{dur(7 * s['mean_lead_weeks'])} {U}" if s["mean_lead_weeks"] else "—",
                help=f"Event date − first sustained alert (replayed {RUNS}, only past data)")
            kpi(":primary[:material/notifications_off:] False alarms", s["false_alarms"],
                help="Alert episodes with no exhaustion inside the predicted window + "
                     f"{config.FALSE_ALARM_GRACE_DAYS} {STEP}s")
            kpi(":primary[:material/bolt:] Spike alerts",
                f"{s['spikes_that_alerted']}/{s['spikes']}")
            kpi(":primary[:material/target:] Forecast error",
                f"{s['mape_pre_event_pct']:.1f}%" if s["mape_pre_event_pct"] is not None else "—",
                help=f"MAPE of the P50 forecast over the {config.MAPE_HOLDOUT_DAYS} {STEP}s "
                     f"before each event. Over the last {config.MAPE_HOLDOUT_DAYS} {STEP}s of "
                     f"data: {s['mape_last30_pct']:.1f}%")
        with panel("Lead time per historical exhaustion event", ":material/timer:"):
            if len(v["leads"]):
                lw = v["leads"].dropna(subset=["lead_weeks"])
                fig = go.Figure(go.Bar(
                    x=[f"{r.rack} {r.resource} ({r.event_date})" for r in lw.itertuples()],
                    y=7 * lw.lead_weeks / UNIT_DAYS, marker_color=PALETTE[:len(lw)],
                    text=(7 * lw.lead_weeks / UNIT_DAYS).map(("{:.1f} " + U).format),
                    textposition="outside",
                    marker_cornerradius=4, textfont=dict(color=UI["text"])))
                plot(styled(fig, height=260, legend=False).update_yaxes(title=f"{U_LONG} of warning"))
            st.dataframe(v["leads"], hide_index=True,
                         column_order=["event_date", "rack", "resource", "lead_weeks",
                                       "first_alert", "first_severity",
                                       "predicted_eta_at_first_alert", "description"])
        e1, e2 = st.columns([3, 2])
        with e1, panel("Every alert episode in the replay", ":material/notifications:"):
            st.dataframe(v["episodes"], hide_index=True)
        with e2, panel("Short spikes", ":material/bolt:", "These should not raise an alert"):
            st.dataframe(v["spikes"], hide_index=True)
        with panel("Forecast accuracy on held-out periods", ":material/target:"):
            st.dataframe(v["accuracy"].round(2), hide_index=True)

    with tab4:
        new_tab()
        log, log_file = audit.tail(200), audit.log_path()
        if log_file.is_relative_to(config.ROOT):
            log_file = log_file.relative_to(config.ROOT)
        with panel(f"Latest {len(log)} entries · {log_file}", ":material/history:",
                   "Append-only; one line per forecast with the hash of its input data."):
            if log:
                st.dataframe(pd.json_normalize(log), hide_index=True)

else:
    # ---------------- tenant view: only this tenant's own rows, no capacity, no rack totals
    st.caption(":material/shield_person: You see only your own workloads; rack capacity and "
               "other tenants' usage are not shown.")
    tab1, tab2 = st.tabs([":material/dashboard: My trends", ":material/query_stats: Detail"])
    with tab1:
        new_tab()
        cards = [r for r in results if r["fc"]]
        for i in range(0, len(cards), 4):
            for col, r in zip(st.columns(4), cards[i:i + 4]):
                with col:
                    kpi(f"{r['rack']} · {r['resource']}", f"{r['fc']['last_level']:.1f}",
                        delta=round(UNIT_DAYS * r["fc"]["slope"], 2) + 0.0,
                        delta_description=f"per {U_LONG[:-1]}")
        own = series_for(viewer, h)[1]
        mine = {WL: res for WL, res in WORKLOADS.items() if any(r["resource"] == res for r in results)}
        with panel("My demand by workload", ":material/timeline:",
                   "Actual (solid) and forecast (dashed)"):
            wl = st.segmented_control("Workload", list(mine), default=next(iter(mine)),
                                      key="t_wl") or next(iter(mine))
            res = mine[wl]
            items = [{"name": r["rack"], "raw": own[(r["rack"], res, viewer)], "fc": r["fc"],
                      "color": TENANT_COLOR_LIST[i % len(TENANT_COLOR_LIST)]}
                     for i, r in enumerate(sorted((r for r in results if r["resource"] == res),
                                                  key=lambda r: r["rack"]))]
            plot(trend_chart(items, f"{wl} ({config.UNITS.get(res, '')})", cutoff, height=420))
        with panel("My usage", ":material/table_chart:"):
            st.dataframe(pd.DataFrame([{
                "Rack": r["rack"], "Resource": r["resource"],
                "Current": round(r["fc"]["last_level"], 1) if r["fc"] else None,
                f"Trend per {U_LONG[:-1]}": round(UNIT_DAYS * r["fc"]["slope"], 2) if r["fc"] else None,
                f"Projected in {84 // UNIT_DAYS} {U}": round(r["fc"]["p50"][83], 1) if r["fc"] else None}
                for r in results]), hide_index=True)
    with tab2:
        new_tab()
        keys = [(r["rack"], r["resource"]) for r in results]
        rack, res = st.selectbox("Series", keys, format_func=lambda k: f"{k[0]} · {k[1]}")
        r = next(x for x in results if (x["rack"], x["resource"]) == (rack, res))
        raw = series_for(viewer, h)[1][(rack, res, viewer)]
        with panel(f"{viewer} · {rack} · {res}", ":material/show_chart:"):
            plot(chart(raw, r["fc"], "Your usage and forecast", config.UNITS.get(res, "")))
