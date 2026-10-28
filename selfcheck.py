"""Run the whole pipeline + backtest + both app views on the current data, and assert the
results are sane. Usage: python selfcheck.py   (exit code 0 = pass)"""
import sys
import time

import numpy as np
import pandas as pd

import config
from pipeline import audit, backtest, clean, engine, loader

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 12)
pd.set_option("display.max_colwidth", 45)
failures = []


def check(cond, msg, target=False):
    """target=True: a goal for the SYNTHETIC scenario. On real data it is only a warning -
    do not tune the model just to make it pass."""
    if not cond and target and source == "real":
        print("  WARN  " + msg)
        return
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


t0 = time.time()
tel, lim, ev, source = loader.load()
print(f"Data source: {source} | telemetry {len(tel):,} rows | limits {len(lim)} | events {len(ev)}")
rack_series, tenant_series = clean.build_series(tel)
cutoff = tel["timestamp"].max().normalize()

print("\n== Live operator run ==")
results = engine.run_operator(rack_series, tenant_series, lim, cutoff)
for r in sorted(results, key=lambda r: (r["eta"]["likely"] is None, r["eta"]["likely"] or 0)):
    if r["status"] != "OK":
        print(f"  {r['status']:8} {r['text']}  | driver: {r['driver']}")
check(len(results) == len(rack_series), "every rack x resource has a result")
check(any(r["status"] in ("CRITICAL", "WARNING", "PLAN") for r in results),
      "at least one active alert at the end of the data", target=True)

print("\n== Tenant isolation ==")
for tenant in sorted(tel["tenant"].dropna().unique()):
    _, ts = clean.build_series(tel[tel["tenant"] == tenant])
    res = engine.run_tenant(tenant, ts, cutoff)
    check(all(k[2] == tenant for k in ts) and len(res) == len(ts),
          f"{tenant}: {len(ts)} series, all its own")

print("\n== Backtest ==")
v = backtest.run(rack_series, lim, ev)
s = v["summary"]
print("event types found:", sorted(ev.event_type.unique()))
print(v["leads"].drop(columns="description").to_string(index=False))
print(v["episodes"].to_string(index=False))
print("summary:", s)
check(s["events"] > 0, "events log has exhaustion events (else fix EXHAUSTION_EVENT_TYPES)",
      target=True)
if s["events"]:
    check(s["events_detected"] == s["events"], "every exhaustion event got an alert beforehand",
          target=True)
    check(v["leads"].lead_weeks.fillna(0).gt(0).all(), "lead time > 0 for every event",
          target=True)
check(s["false_alarms"] <= 1, f"false alarms <= 1 (got {s['false_alarms']})", target=True)
check(s["spikes_that_alerted"] == 0, "no short spike raised an alert", target=True)
check(np.isfinite(s["mape_last30_pct"]), f"MAPE finite ({s['mape_last30_pct']}%)")

print("\n== Audit ==")
check(audit.LOG.exists() and len(audit.tail(5)) > 0, f"audit log written to {audit.LOG}")

print("\n== Streamlit app (AppTest) ==")
try:
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("app.py", default_timeout=600).run()
    check(not at.exception, f"operator view renders without exceptions {list(at.exception)}")
    tenant = sorted(tel["tenant"].dropna().unique())[0]
    at.sidebar.selectbox[0].select(tenant).run()
    check(not at.exception, f"tenant view renders without exceptions {list(at.exception)}")
    text = " ".join(m.value for m in at.markdown) + " ".join(str(d.value) for d in at.dataframe)
    others = [t for t in tel["tenant"].dropna().unique() if t != tenant]
    check(not any(t in text for t in others), "tenant view never mentions other tenants")
except Exception as exc:  # noqa: BLE001 - report, do not crash the selfcheck
    check(False, f"AppTest failed: {exc!r}")

print(f"\nDone in {time.time() - t0:.0f}s — {'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
sys.exit(1 if failures else 0)
