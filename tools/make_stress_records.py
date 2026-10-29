"""Write three stress-test records to raw_data/dc_stress_*/ (pick them in the Data centre menu).
Each one targets a different part of the pipeline and is built to expose a known weakness:

  1  smoothing + alert rule   4-5 day spikes outlast the 7-day median, so 3 runs in a row alert
  2  forecast model           accelerating growth and a late step: the linear fit warns late
  3  ingestion / limits       limit rows whose names differ by case/space are silently dropped,
                              and a stuck (flatlined) sensor gives a zero-width band

usage: python tools/make_stress_records.py      (seeded, so the output is reproducible)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
N_DAYS = 200
START = pd.Timestamp("2026-04-01")
DAYS = pd.date_range(START, periods=N_DAYS, freq="D")
RACKS = ["rack-1", "rack-2", "rack-3", "rack-4"]
TENANTS = ["tenant-A", "tenant-B", "tenant-C"]
LIMITS = {"power_kw": (40.0, 4.0), "temp_c": (35.0, 3.0), "storage_tb": (500.0, 50.0)}
d = np.arange(N_DAYS)


def base(rng):
    """Quiet baseline: {(rack, resource, tenant or None): daily level array}."""
    lv = {}
    for r in RACKS:
        lv[(r, "power_kw", None)] = np.full(N_DAYS, 26.0) + 0.01 * d
        lv[(r, "temp_c", None)] = np.full(N_DAYS, 24.0)
        for t in TENANTS:
            lv[(r, "storage_tb", t)] = np.full(N_DAYS, 90.0) + 0.05 * d
    return lv


def frame(lv, rng, noise=0.02):
    rows = []
    for (rack, res, ten), x in lv.items():
        v = x * (1 + rng.normal(0, noise, N_DAYS)) if noise else x
        rows.append(pd.DataFrame({"timestamp": DAYS, "rack": rack, "tenant": ten,
                                  "resource": res, "value": np.round(v, 3)}))
    return pd.concat(rows, ignore_index=True)


def limits(rename=None):
    rows = [(r, res, c, m) for r in RACKS for res, (c, m) in LIMITS.items()]
    df = pd.DataFrame(rows, columns=["rack", "resource", "capacity", "safety_margin"])
    if rename:
        df["rack"] = df["rack"].replace(rename)
    return df


def ev(day, rack, res, kind, text):
    return (START + pd.Timedelta(days=day), rack, res, kind, text)


def stress_1(rng):
    """Spikes of 3 / 4 / 5 days, all well above the threshold, all resolving on their own."""
    lv = base(rng)
    spikes = [("rack-1", "temp_c", None, 60, 3, 11.0),          # control: should stay quiet
              ("rack-2", "temp_c", None, 90, 4, 11.0),
              ("rack-3", "power_kw", None, 120, 5, 14.0),
              ("rack-4", "temp_c", None, 150, 5, 11.0)]
    events = []
    for rack, res, ten, start, n, amt in spikes:
        lv[(rack, res, ten)] = lv[(rack, res, ten)] + np.where((d >= start) & (d < start + n), amt, 0)
        events.append(ev(start, rack, res, "transient_spike",
                         f"{n}-day {res} spike on {rack} (cooling unit swap); resolved without intervention"))
    # one real exhaustion so lead time is still measurable: rack-1 storage, tenant-B grows
    lv[("rack-1", "storage_tb", "tenant-B")] += np.clip(d - 40, 0, None) * 1.6
    cross = first_cross(lv, "rack-1", "storage_tb", 450)
    events.append(ev(cross, "rack-1", "storage_tb", "exhaustion",
                     "Storage full on rack-1: tenant-B backups; emergency cleanup"))
    lv[("rack-1", "storage_tb", "tenant-B")][cross:] -= 150
    return frame(lv, rng), limits(), events, {
        "name": "Stress 1 · long spikes",
        "targets": "forecast.smooth (7-day median) + alerts.classify (3 sustained runs)",
        "expected": "4- and 5-day spikes raise sustained alerts -> false alarms; the 3-day "
                    "spike on rack-1 temp_c stays quiet (control). Also: exactly one live alert "
                    "makes the Overview draw the same gauge twice -> duplicate plotly_chart ID crash"}


def stress_2(rng):
    """Growth that a straight line under-forecasts, and a step that lands close to the limit."""
    lv = base(rng)
    # rack-2 storage: tenant-C grows exponentially (doubling every ~25 days)
    k = ("rack-2", "storage_tb", "tenant-C")
    lv[k] = lv[k] + 4.0 * (np.exp(np.clip(d - 50, 0, None) * np.log(2) / 25) - 1)
    cross = first_cross(lv, "rack-2", "storage_tb", 450)
    events = [ev(cross, "rack-2", "storage_tb", "exhaustion",
                 "Storage full on rack-2: tenant-C ML checkpoints grew exponentially")]
    lv[k][cross:] = 90.0
    # rack-3 power: GPU nodes added on day 120 (step + steeper ramp) -> crosses ~5 weeks later
    k = ("rack-3", "power_kw", None)
    lv[k] = lv[k] + np.where(d >= 120, 6.0, 0) + np.clip(d - 120, 0, None) * 0.11
    cross3 = int(np.argmax(lv[k] >= 36.0))
    events.append(ev(120, "rack-3", "power_kw", "tenant_onboarding", "GPU build-out on rack-3"))
    events.append(ev(cross3, "rack-3", "power_kw", "exhaustion",
                     "rack-3 hit its power budget: GPU build-out; jobs throttled"))
    lv[k][cross3:] = 28.0
    return frame(lv, rng), limits(), events, {
        "name": "Stress 2 · accelerating growth",
        "targets": "forecast.forecast (linear trend) + alerts fresh-regime hold",
        "expected": "exponential growth on rack-2 storage and a step + ramp on rack-3 power "
                    "are warned late (short or no lead time)"}


def stress_3(rng):
    """Limits written by hand: rack names that differ only by case or a trailing space."""
    lv = base(rng)
    lv[("rack-2", "storage_tb", "tenant-A")] += np.clip(d - 30, 0, None) * 1.4
    cross = first_cross(lv, "rack-2", "storage_tb", 450)
    events = [ev(cross, "rack-2", "storage_tb", "exhaustion",
                 "Storage full on rack-2: tenant-A log retention changed")]
    lv[("rack-2", "storage_tb", "tenant-A")][cross:] -= 160
    t = frame(lv, rng)
    # rack-4 power sensor stuck: the same reading every day from day 100 on
    stuck = (t.rack == "rack-4") & (t.resource == "power_kw") & (t.timestamp >= DAYS[100])
    t.loc[stuck, "value"] = 33.5
    return t, limits({"rack-2": "Rack-2", "rack-3": "rack-3 "}), events, {
        "name": "Stress 3 · messy limits file",
        "targets": "loader limits matching + engine.threshold; flatlined sensor",
        "expected": "rack-2 and rack-3 vanish from monitoring with no warning, so the rack-2 "
                    "storage exhaustion is missed; rack-4 power has a zero-width band"}


def first_cross(lv, rack, res, thr):
    total = sum(x for (r, s, _), x in lv.items() if (r, s) == (rack, res))
    return int(np.argmax(total >= thr))


def write(n, fn):
    rng = np.random.default_rng(100 + n)
    tel, lim, events, meta = fn(rng)
    out = ROOT / "raw_data" / f"dc_stress_{n}"
    out.mkdir(parents=True, exist_ok=True)
    tel.to_csv(out / "telemetry.csv.gz", index=False)
    lim.to_csv(out / "limits.csv", index=False)
    pd.DataFrame(events, columns=["timestamp", "rack", "resource", "event_type", "description"]
                 ).sort_values("timestamp").to_csv(out / "events.csv", index=False)
    (out / "record.json").write_text(json.dumps({"name": meta["name"], "step": "day"}) + "\n")
    (out / "README.md").write_text(
        f"# {meta['name']}\n\nGenerated by `tools/make_stress_records.py` (seeded).\n\n"
        f"- **Targets:** {meta['targets']}\n- **Expected failure:** {meta['expected']}\n")
    print(out, len(tel), "rows")


if __name__ == "__main__":
    for n, fn in enumerate((stress_1, stress_2, stress_3), 1):
        if len(sys.argv) < 2 or str(n) in sys.argv[1:]:
            write(n, fn)
