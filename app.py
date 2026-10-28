"""Capacity early-warning dashboard (AO-02). Read-only: never changes facility controls."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from pipeline import audit, backtest, clean, engine, loader
from pipeline.exhaustion import weeks
from pipeline.forecast import smooth

st.set_page_config(page_title="Capacity Early Warning", layout="wide")
st.info("🔒 **Read-only:** this tool reads telemetry and never changes facility controls.")

# Translucent tints keep the theme's own text colour readable in both light and dark mode
SEV_COLOR = {"CRITICAL": "rgba(220, 53, 69, 0.35)", "WARNING": "rgba(255, 193, 7, 0.30)",
             "PLAN": "rgba(13, 110, 253, 0.25)", "HOLD": "rgba(108, 117, 125, 0.25)", "OK": None}
SEV_ICON = {"CRITICAL": "🔴", "WARNING": "🟠", "PLAN": "🔵", "HOLD": "⚪", "OK": "🟢"}
SEV_ORDER = {"CRITICAL": 0, "WARNING": 1, "PLAN": 2, "HOLD": 3, "OK": 4}


# ---------------------------------------------------------------- cached data access
@st.cache_data(show_spinner="Loading telemetry…")
def load():
    tel, lim, ev, source = loader.load()
    return tel, lim, ev, source, loader.data_hash(tel)


@st.cache_data(show_spinner=False)
def series_for(viewer, data_hash):
    """TENANT ISOLATION: a tenant's rows are filtered out BEFORE any computation, and a
    tenant view never gets rack totals (they would reveal other tenants' usage)."""
    tel = load()[0]
    if viewer != "Operator":
        tel = tel[tel["tenant"] == viewer]
        return {}, clean.build_series(tel)[1]
    return clean.build_series(tel)


@st.cache_data(show_spinner="Forecasting…")
def operator_run(data_hash, cutoff):
    rack_series, tenant_series = series_for("Operator", data_hash)
    return engine.run_operator(rack_series, tenant_series, load()[1], cutoff)  # audited once


@st.cache_data(show_spinner="Forecasting…")
def tenant_run(viewer, data_hash, cutoff):
    return engine.run_tenant(viewer, series_for(viewer, data_hash)[1], cutoff)


@st.cache_data(show_spinner="Back-testing every day of history (cached after first run)…")
def validation(data_hash):
    rack_series, _ = series_for("Operator", data_hash)
    _, lim, ev, _, _ = load()
    return backtest.run(rack_series, lim, ev)


# ---------------------------------------------------------------- helpers
def fmt_weeks(d):
    return "—" if d is None else ("now" if d == 0 else f"{weeks(d)}")


def eta_range(e):
    if e["likely"] is None and e["earliest"] is None:
        return "beyond horizon"
    latest = fmt_weeks(e["latest"]) if e["latest"] is not None else f">{config.HORIZON_DAYS // 7}"
    return f"{fmt_weeks(e['earliest'])}–{latest} wks"


def chart(raw, fc, title, unit, cap=None, thr=None, events=None):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=raw.index, y=raw.values, name="Daily", mode="lines",
                             line=dict(color="rgba(150, 150, 150, 0.6)", width=1)))
    fig.add_trace(go.Scatter(x=raw.index, y=smooth(raw).values, name="7-day median",
                             line=dict(color="#4ea8ff", width=2.5)))
    if fc is not None:
        x = list(fc["dates"]) + list(fc["dates"][::-1])
        fig.add_trace(go.Scatter(x=x, y=list(fc["p90"]) + list(fc["p10"][::-1]), fill="toself",
                                 fillcolor="rgba(78,168,255,0.22)", line=dict(width=0),
                                 name="P10–P90", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=fc["dates"], y=fc["p50"], name="P50 forecast",
                                 line=dict(color="#4ea8ff", dash="dot", width=2)))
    if cap is not None:
        fig.add_hline(y=cap, line=dict(color="red", dash="dash"), annotation_text="capacity")
        fig.add_hline(y=thr, line=dict(color="orange", dash="dash"),
                      annotation_text="capacity − safety margin")
    marks = [(raw.index[-1], "today", "#9e9e9e")]
    if fc is not None:
        marks += [(c, "change point", "#b388ff") for c in fc["change_points"]]
    for d, label, color in marks:
        fig.add_shape(type="line", x0=d, x1=d, yref="paper", y0=0, y1=1,
                      line=dict(color=color, dash="dot", width=1))
        fig.add_annotation(x=d, yref="paper", y=1.04, text=label, showarrow=False,
                           font=dict(color=color, size=10))
    if events is not None and len(events):
        y = raw.reindex(events.timestamp.dt.normalize()).values
        fig.add_trace(go.Scatter(x=events.timestamp, y=y, mode="markers", name="Past events",
                                 marker=dict(symbol="x", size=12, color="crimson"),
                                 text=events.event_type + ": " + events.description,
                                 hoverinfo="text+x"))
    fig.update_layout(title=title, yaxis_title=unit, height=480, hovermode="x unified",
                      legend=dict(orientation="h", y=-0.15), margin=dict(t=60))
    return fig


def explanation(r):
    e, res, rack = r["eta"], r["resource"], r["rack"]
    if e["likely"] == 0:
        head = f"**{res} on {rack} is already past its safety margin.**"
    elif e["likely"] is not None:
        head = (f"**{res} on {rack} will likely run out in ~{weeks(e['likely'])} weeks** "
                f"({eta_range(e)}).")
    elif e["earliest"] is not None:
        head = (f"{res} on {rack} is not expected to run out within "
                f"{config.HORIZON_DAYS // 7} weeks; worst case {weeks(e['earliest'])} weeks.")
    else:
        head = f"{res} on {rack} has no forecast exhaustion within {config.HORIZON_DAYS // 7} weeks."
    top, fc = r["top"], r["fc"]
    if top is not None:
        since = ""
        if fc["change_points"] and fc["fit_start"] == fc["change_points"][-1]:
            since = f" since {fc['fit_start']:%d %b %Y}, when the trend changed"
        why = f" {top['share_pct']:.0f}% of the growth comes from **{top['tenant']}**{since}."
    elif r["shares"] == [] and "rack-level" in r["driver"]:
        why = " This is a rack-level metric, so it cannot be split by tenant."
    else:
        why = " No tenant is growing significantly."
    note = ""
    if r["status"] == "HOLD":
        note = (" A crossing is forecast but not yet confirmed (needs 3 consecutive daily runs "
                "and 3 weeks of data after any change point).")
    return f"{head}{why}{note}\n\n**Recommended:** {r['recommendation']}"


# ---------------------------------------------------------------- page
tel, lim, ev, source, h = load()
tenants = sorted(tel["tenant"].dropna().unique())
viewer = st.sidebar.selectbox("View as", ["Operator"] + tenants,
                              help="Simulates login. A tenant sees only their own usage.")
cutoff = tel["timestamp"].max().normalize()
st.sidebar.caption(f"Data source: **{source}** · data as of **{cutoff:%d %b %Y}**")
st.sidebar.caption(f"Forecast horizon {config.HORIZON_DAYS // 7} weeks · alerts need "
                   f"{config.SUSTAIN_RUNS} consecutive daily runs")
st.title("Seeing trouble before the capacity runs out")

if viewer == "Operator":
    results = operator_run(h, cutoff)
    tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Resource detail", "Validation", "Audit log"])

    with tab1:
        rows = sorted(results, key=lambda r: (SEV_ORDER[r["status"]],
                                              r["eta"]["likely"] if r["eta"]["likely"] is not None else 9e9))
        df = pd.DataFrame([{
            "Status": f"{SEV_ICON[r['status']]} {r['status']}", "Rack": r["rack"],
            "Resource": r["resource"],
            "Likely ETA (wks)": fmt_weeks(r["eta"]["likely"]), "Range": eta_range(r["eta"]),
            "Used now": f"{100 * r['fc']['last_level'] / r['capacity']:.0f}%" if r["fc"] else "—",
            "Top driver": r["driver"], "Recommended decision": r["recommendation"]}
            for r in rows])
        statuses = [r["status"] for r in rows]
        c = st.columns(4)
        for col, s in zip(c, ["CRITICAL", "WARNING", "PLAN", "HOLD"]):
            col.metric(f"{SEV_ICON[s]} {s}", statuses.count(s), border=True)

        def tint(row):
            color = SEV_COLOR[statuses[row.name]]
            return [f"background-color: {color}" if color else ""] * len(row)

        st.dataframe(df.style.apply(tint, axis=1), hide_index=True,
                     height=35 * (len(df) + 1) + 3,
                     column_config={"Top driver": st.column_config.TextColumn(width="medium"),
                                    "Recommended decision":
                                        st.column_config.TextColumn(width="large")})

    with tab2:
        c1, c2 = st.columns(2)
        racks = sorted({r["rack"] for r in results})
        rack = c1.selectbox("Rack", racks)
        res = c2.selectbox("Resource", sorted({r["resource"] for r in results if r["rack"] == rack}))
        r = next(x for x in results if x["rack"] == rack and x["resource"] == res)
        raw = series_for("Operator", h)[0][(rack, res)]
        evs = ev[(ev.rack == rack) & (ev.resource == res)]
        st.plotly_chart(chart(raw, r["fc"], f"{rack} · {res} — {r['status']}",
                              config.UNITS.get(res, ""), r["capacity"], r["threshold"], evs))
        st.markdown(explanation(r))
        if r["shares"]:
            sh = pd.DataFrame(r["shares"])
            st.plotly_chart(go.Figure(go.Bar(x=sh.share_pct, y=sh.tenant, orientation="h",
                                             marker_color="#4ea8ff",
                                             text=sh.share_pct.map("{:.0f}%".format),
                                             textposition="outside"))
                            .update_layout(title="Share of growth by tenant (%)", height=260,
                                           yaxis=dict(autorange="reversed")))
        if r["fc"]:
            fc = r["fc"]
            st.caption(f"Fit window {fc['fit_start']:%d %b %Y} → {fc['fit_end']:%d %b %Y} "
                       f"({fc['n_fit']} days) · slope {fc['slope']:+.3f} {config.UNITS.get(res, '')}/day"
                       f" · residual σ {fc['resid_std']:.3f} · input sha256 {fc['input_sha256'][:12]}…")

    with tab3:
        if not ev.event_type.isin({t.lower() for t in config.EXHAUSTION_EVENT_TYPES}).any():
            st.warning("No exhaustion events matched, so lead time cannot be measured. "
                       f"Event types in the log: {sorted(ev.event_type.unique())} — add the "
                       "right ones to EXHAUSTION_EVENT_TYPES in config.py.")
        v = validation(h)
        s = v["summary"]
        c = st.columns(5)
        c[0].metric("Events caught", f"{s['events_detected']}/{s['events']}")
        c[1].metric("Mean lead time", f"{s['mean_lead_weeks']} wks" if s["mean_lead_weeks"] else "—",
                    help="Event date − first sustained alert (replayed daily, only past data)")
        c[2].metric("False alarms", s["false_alarms"],
                    help="Alert episodes with no exhaustion inside the predicted window + 14 days")
        c[3].metric("Spikes that alerted", f"{s['spikes_that_alerted']}/{s['spikes']}")
        c[4].metric("Forecast error", f"{s['mape_pre_event_pct']:.1f}%",
                    help=f"MAPE of the P50 forecast over the 30 days before each event. "
                         f"Over the last 30 days of data: {s['mape_last30_pct']:.1f}%")
        st.subheader("Lead time per historical exhaustion event")
        st.dataframe(v["leads"], hide_index=True,
                     column_order=["event_date", "rack", "resource", "lead_weeks",
                                   "first_alert", "first_severity",
                                   "predicted_eta_at_first_alert", "description"])
        st.subheader("Every alert episode in the replay")
        st.dataframe(v["episodes"], hide_index=True)
        st.subheader("Short spikes (should NOT alert)")
        st.dataframe(v["spikes"], hide_index=True)
        st.subheader("Forecast accuracy on held-out periods")
        st.dataframe(v["accuracy"].round(2), hide_index=True)

    with tab4:
        log = audit.tail(200)
        st.caption(f"Latest {len(log)} entries from `audit/{audit.LOG.name}` "
                   "(append-only; one line per forecast with the hash of its input data).")
        if log:
            st.dataframe(pd.json_normalize(log), hide_index=True)

else:
    # ---------------- tenant view: only this tenant's own rows, no capacity, no rack totals
    results = tenant_run(viewer, h, cutoff)
    st.subheader(f"Your usage — {viewer}")
    st.caption("You see only your own workloads. Rack capacity and other tenants' usage are "
               "not shown.")
    tab1, tab2 = st.tabs(["My trends", "Detail"])
    with tab1:
        st.dataframe(pd.DataFrame([{
            "Rack": r["rack"], "Resource": r["resource"],
            "Current": round(r["fc"]["last_level"], 1) if r["fc"] else None,
            "Trend per week": round(7 * r["fc"]["slope"], 2) if r["fc"] else None,
            "Projected in 12 wks": round(r["fc"]["p50"][83], 1) if r["fc"] else None}
            for r in results]), hide_index=True)
    with tab2:
        keys = [(r["rack"], r["resource"]) for r in results]
        rack, res = st.selectbox("Series", keys, format_func=lambda k: f"{k[0]} · {k[1]}")
        r = next(x for x in results if (x["rack"], x["resource"]) == (rack, res))
        raw = series_for(viewer, h)[1][(rack, res, viewer)]
        st.plotly_chart(chart(raw, r["fc"], f"{viewer} · {rack} · {res}",
                              config.UNITS.get(res, "")))
