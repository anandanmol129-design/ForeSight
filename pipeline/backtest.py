"""Replay the live pipeline day by day using only data available at each cutoff.

1. scan(): for every rack x resource and every daily cutoff, run the SAME forecast +
   exhaustion code the app uses (data <= cutoff only). Cached to disk.
2. Sustained alert = P50 crossing on SUSTAIN_RUNS consecutive cutoffs (as in alerts.py).
3. Lead time per exhaustion event = event date - first sustained alert in the 120 days before.
4. False alarm = an alert episode with no exhaustion on that rack/resource between its
   first day and the end of its predicted window (P10 ETA, or horizon) + 14 days.
5. MAPE of the P50 path on the 30 days before each event, and on the last 30 days.
"""
import hashlib
import pickle

import numpy as np
import pandas as pd

import config
from pipeline import alerts, audit, engine, exhaustion
from pipeline.forecast import forecast

PARAMS = ["ROLL_DAYS", "MIN_FIT_DAYS", "FALLBACK_FIT_DAYS", "MAX_FIT_DAYS", "HORIZON_DAYS",
          "CP_MIN_SIZE", "CP_JUMP", "CP_PENALTY", "Z_80", "SUSTAIN_RUNS",
          "BACKTEST_STEP_DAYS", "BACKTEST_START_DAY"]


def _key(rack_series, limits):
    h = hashlib.sha256(repr([getattr(config, p) for p in PARAMS]).encode())
    for k, s in rack_series.items():
        h.update(repr(k).encode() + s.values.astype("float64").tobytes())
    h.update(limits.to_csv().encode())
    return h.hexdigest()[:16]


def scan(rack_series, limits):
    """DataFrame: rack, resource, cutoff, earliest, likely, latest (days), status."""
    cache = config.CACHE_DIR / f"scan_{_key(rack_series, limits)}.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    rows, records, run_id = [], [], audit.new_run_id()
    for (rack, res), raw in rack_series.items():
        _, thr = engine.threshold(limits, rack, res)
        if thr is None:
            continue
        start = raw.index[0] + pd.Timedelta(days=config.BACKTEST_START_DAY)
        for cutoff in pd.date_range(start, raw.index[-1], freq=f"{config.BACKTEST_STEP_DAYS}D"):
            fc = forecast(raw, cutoff)
            e = exhaustion.eta(fc, thr)
            rows.append({"rack": rack, "resource": res, "cutoff": cutoff, **e,
                         "fresh": fc is not None and fc["level_shift_at"] is not None})
            records.append(audit.record(run_id, "backtest", rack, res, fc, e,
                                        run_type="backtest"))
    if not rows:
        raise ValueError("No rack/resource series matched a row in the limits table - check "
                         "rack and resource names in limits vs telemetry (config.py)")
    df = pd.DataFrame(rows)
    df["status"] = _statuses(df)
    config.CACHE_DIR.mkdir(exist_ok=True)
    df.to_pickle(cache)
    audit.append(records, config.AUDIT_DIR / "backtest_log.jsonl")
    return df


def _statuses(df):
    """Apply the live alert rule (alerts.classify) over consecutive cutoffs of each series."""
    status = pd.Series("OK", index=df.index)
    for _, g in df.groupby(["rack", "resource"]):
        likely = [None if pd.isna(x) else x for x in g["likely"]]
        fresh = g["fresh"].tolist()
        status[g.index] = [alerts.classify(likely[max(0, i - config.SUSTAIN_RUNS + 1):i + 1],
                                           fresh[i]) for i in range(len(likely))]
    return status


def _exhaustions(events):
    return events[events.event_type.isin({t.lower() for t in config.EXHAUSTION_EVENT_TYPES})]


def lead_times(sc, events):
    rows = []
    for _, ev in _exhaustions(events).iterrows():
        ed = ev.timestamp.normalize()
        g = sc[(sc.rack == ev.rack) & (sc.resource == ev.resource)
               & (sc.cutoff >= ed - pd.Timedelta(days=config.BACKTEST_LOOKBACK_DAYS))
               & (sc.cutoff < ed)]
        alerted = g[g.status.isin(["CRITICAL", "WARNING", "PLAN"])]
        first = alerted.iloc[0] if len(alerted) else None
        rows.append({
            "event_date": ed.date(), "rack": ev.rack, "resource": ev.resource,
            "description": ev.description,
            "first_alert": None if first is None else first.cutoff.date(),
            "first_severity": None if first is None else first.status,
            "lead_weeks": None if first is None else round((ed - first.cutoff).days / 7, 1),
            "predicted_eta_at_first_alert": None if first is None else
                f"{exhaustion.weeks(first.likely)} wks "
                f"({exhaustion.weeks(first.earliest)}–{exhaustion.weeks(first.latest)})",
        })
    return pd.DataFrame(rows, columns=["event_date", "rack", "resource", "description",
                                       "first_alert", "first_severity", "lead_weeks",
                                       "predicted_eta_at_first_alert"])


def alert_episodes(sc, events, data_end):
    """Each run of consecutive alert days on a series, judged against the events log."""
    ex = _exhaustions(events)
    rows = []
    for (rack, res), g in sc.groupby(["rack", "resource"]):
        on = g.status.isin(["CRITICAL", "WARNING", "PLAN"]).values
        starts = np.nonzero(on & ~np.r_[False, on[:-1]])[0]
        for i in starts:
            first = g.iloc[i]
            length = int(np.argmin(np.r_[on[i:], False]))
            span = first.latest if pd.notna(first.latest) else config.HORIZON_DAYS
            w_end = first.cutoff + pd.Timedelta(days=span + config.FALSE_ALARM_GRACE_DAYS)
            hit = ex[(ex.rack == rack) & (ex.resource == res)
                     & (ex.timestamp >= first.cutoff - pd.Timedelta(days=1))
                     & (ex.timestamp <= w_end)]
            verdict = ("true alarm" if len(hit) else
                       "pending (window still open)" if w_end > data_end else "FALSE ALARM")
            rows.append({"rack": rack, "resource": res, "alert_start": first.cutoff.date(),
                         "days_active": length, "severity": first.status,
                         "window_end": w_end.date(), "verdict": verdict})
    return pd.DataFrame(rows, columns=["rack", "resource", "alert_start", "days_active",
                                       "severity", "window_end", "verdict"])


def spike_check(sc, events):
    """Did any short, self-resolving spike trigger a sustained alert within 7 days?"""
    rows = []
    spikes = events[events.event_type.isin({t.lower() for t in config.SPIKE_EVENT_TYPES})]
    for _, ev in spikes.iterrows():
        g = sc[(sc.rack == ev.rack) & (sc.resource == ev.resource)
               & (sc.cutoff >= ev.timestamp.normalize())
               & (sc.cutoff <= ev.timestamp + pd.Timedelta(days=7))]
        rows.append({"spike_date": ev.timestamp.date(), "rack": ev.rack,
                     "resource": ev.resource,
                     "raised_alert": bool(g.status.isin(["CRITICAL", "WARNING", "PLAN"]).any()),
                     "held_days": int((g.status == "HOLD").sum())})
    return pd.DataFrame(rows, columns=["spike_date", "rack", "resource", "raised_alert",
                                       "held_days"])


def mape(raw, cutoff, days=config.MAPE_HOLDOUT_DAYS):
    fc = forecast(raw, cutoff)
    actual = raw.loc[cutoff + pd.Timedelta(days=1):cutoff + pd.Timedelta(days=days)]
    if fc is None or actual.empty:
        return None
    pred = pd.Series(fc["p50"], index=fc["dates"]).reindex(actual.index)
    ok = actual.abs() > 1e-9
    return float(100 * np.mean(np.abs((pred[ok] - actual[ok]) / actual[ok])))


def accuracy(rack_series, events):
    rows = []
    for _, ev in _exhaustions(events).iterrows():
        raw = rack_series.get((ev.rack, ev.resource))
        if raw is not None:
            c = ev.timestamp.normalize() - pd.Timedelta(days=config.MAPE_HOLDOUT_DAYS + 1)
            rows.append({"period": f"30d before {ev.timestamp:%Y-%m-%d} event",
                         "rack": ev.rack, "resource": ev.resource, "mape_pct": mape(raw, c)})
    for (rack, res), raw in rack_series.items():
        c = raw.index[-1] - pd.Timedelta(days=config.MAPE_HOLDOUT_DAYS)
        rows.append({"period": "last 30 days", "rack": rack, "resource": res,
                     "mape_pct": mape(raw, c)})
    return pd.DataFrame(rows)


def _round(x, n=1):
    return None if x is None or pd.isna(x) else round(float(x), n)


def run(rack_series, limits, events):
    data_end = max(s.index[-1] for s in rack_series.values())
    sc = scan(rack_series, limits)
    leads = lead_times(sc, events)
    eps = alert_episodes(sc, events, data_end)
    spikes = spike_check(sc, events)
    acc = accuracy(rack_series, events)
    ev_acc = acc[acc.period != "last 30 days"].mape_pct
    summary = {
        "events": len(leads),
        "events_detected": int(leads.lead_weeks.notna().sum()) if len(leads) else 0,
        "mean_lead_weeks": _round(leads.lead_weeks.mean()),
        "min_lead_weeks": _round(leads.lead_weeks.min()),
        "alert_episodes": len(eps),
        "false_alarms": int((eps.verdict == "FALSE ALARM").sum()),
        "spikes": len(spikes),
        "spikes_that_alerted": int(spikes.raised_alert.sum()) if len(spikes) else 0,
        "mape_pre_event_pct": round(float(ev_acc.mean()), 2) if ev_acc.notna().any() else None,
        "mape_last30_pct": round(float(acc[acc.period == "last 30 days"].mape_pct.mean()), 2),
    }
    return {"summary": summary, "leads": leads, "episodes": eps, "spikes": spikes,
            "accuracy": acc, "scan": sc}
