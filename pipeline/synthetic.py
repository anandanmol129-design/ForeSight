"""Synthetic 12-month hourly telemetry in the canonical format (used when ./data/ is empty).

Scenario (day numbers from the start of the data):
  * 3 real exhaustion events with gradual growth, each followed by a mitigation drop:
      rack-1 storage_tb   tenant-B grows from day 40      -> crosses ~day 165
      rack-3 power_kw     GPU build-out ramps from day 130 -> crosses ~day 240
      rack-2 vm_count     tenant-D grows from day 200      -> crosses ~day 300
  * regime change: tenant-E onboards rack-4 on day 290 (step + steeper slope)
  * still-growing series at the end so the demo shows CRITICAL / WARNING / PLAN
  * 5 short spikes that resolve within 1-3 days (logged as transient_spike)
  * weekly + daily seasonality, multiplicative noise
"""
import numpy as np
import pandas as pd

import config

N_DAYS = 365
END = pd.Timestamp("2026-10-27")
START = END - pd.Timedelta(days=N_DAYS - 1)
RACKS = ["rack-1", "rack-2", "rack-3", "rack-4"]
TENANTS = ["tenant-A", "tenant-B", "tenant-C", "tenant-D", "tenant-E"]

LIMITS = {  # resource: (capacity, safety_margin) - same for every rack
    "power_kw": (40.0, 4.0), "temp_c": (35.0, 3.0), "storage_tb": (500.0, 50.0),
    "network_gbps": (100.0, 10.0), "vm_count": (400.0, 40.0),
}
# Baseline per (rack, tenant): (storage_tb, network_gbps, vm_count)
BASE = {
    ("rack-1", "tenant-A"): (120, 20, 70), ("rack-1", "tenant-B"): (150, 25, 80),
    ("rack-1", "tenant-C"): (100, 15, 60),
    ("rack-2", "tenant-B"): (110, 18, 90), ("rack-2", "tenant-C"): (90, 20, 80),
    ("rack-2", "tenant-D"): (100, 22, 100),
    ("rack-3", "tenant-A"): (130, 15, 70), ("rack-3", "tenant-C"): (90, 20, 75),
    ("rack-3", "tenant-D"): (110, 20, 75),
    ("rack-4", "tenant-A"): (100, 15, 60), ("rack-4", "tenant-B"): (120, 20, 70),
    ("rack-4", "tenant-D"): (110, 18, 70), ("rack-4", "tenant-E"): (0, 0, 0),
}
SPIKES = [  # (rack, resource, tenant or None, start_day, n_days, added amount)
    ("rack-2", "temp_c", None, 100, 2, 9.0),
    ("rack-4", "network_gbps", "tenant-B", 150, 1, 45.0),
    ("rack-3", "storage_tb", "tenant-A", 210, 3, 110.0),
    ("rack-1", "power_kw", None, 275, 2, 14.0),
    ("rack-4", "vm_count", "tenant-A", 320, 2, 160.0),
]


def ramp(day, start, rate, stop=None):
    """Linear growth of `rate`/day from `start`, frozen at `stop` (if given)."""
    end = day if stop is None else np.minimum(day, stop)
    return np.clip(end - start, 0, None) * rate


def first_cross(level, threshold, after):
    idx = np.where((level >= threshold) & (np.arange(len(level)) >= after))[0]
    return int(idx[0]) if len(idx) else None


def daily_levels():
    """Noise-free daily level per series plus the derived exhaustion events."""
    d = np.arange(N_DAYS, dtype=float)
    lv, events = {}, []
    for (rack, tenant), (s, n, v) in BASE.items():
        lv[(rack, "storage_tb", tenant)] = np.full(N_DAYS, float(s)) + ramp(d, 0, 0.03)
        lv[(rack, "network_gbps", tenant)] = np.full(N_DAYS, float(n))
        lv[(rack, "vm_count", tenant)] = np.full(N_DAYS, float(v))

    def exhaust(key, rate, start, cut, after, cause):
        """Grow `key` until the rack total crosses the threshold, then mitigate."""
        rack, res, _ = key
        thr = LIMITS[res][0] - LIMITS[res][1]
        others = sum(a for k, a in lv.items() if k[0] == rack and k[1] == res and k != key)
        trial = lv[key] + ramp(d, start, rate)
        day = first_cross(others + trial, thr, after)
        lv[key] = lv[key] + ramp(d, start, rate, stop=day + 3) - np.where(d >= day + 3, cut, 0)
        events.append((day, rack, res, cause))

    # Event 1 & 3 are tenant-driven; event 2 (power) is added after power is derived below.
    exhaust(("rack-1", "storage_tb", "tenant-B"), 0.65, 40, 140, 100,
            "Storage full on rack-1: tenant-B data lake growth; emergency cleanup + migration")
    exhaust(("rack-2", "vm_count", "tenant-D"), 0.95, 200, 110, 250,
            "VM placement failures on rack-2: tenant-D scale-out; VMs migrated to rack-3")

    # Regime change: tenant-E onboards rack-4 on day 290 (step + steep growth)
    on = (d >= 290).astype(float)
    lv[("rack-4", "storage_tb", "tenant-E")] = on * 30 + ramp(d, 290, 0.5)
    lv[("rack-4", "network_gbps", "tenant-E")] = on * 6 + ramp(d, 290, 0.05)
    lv[("rack-4", "vm_count", "tenant-E")] = on * 25 + ramp(d, 290, 0.2)
    # Still growing at the end: rack-1 network (tenant-C) -> CRITICAL; rack-2 storage -> PLAN
    lv[("rack-1", "network_gbps", "tenant-C")] += ramp(d, 250, 0.24)
    lv[("rack-2", "storage_tb", "tenant-C")] += ramp(d, 150, 0.3)

    # Rack-level power/temperature follow the VM count; rack-3 gets a GPU build-out
    for rack in RACKS:
        vms = sum(a for k, a in lv.items() if k[0] == rack and k[1] == "vm_count")
        lv[(rack, "power_kw", None)] = 18 + 0.03 * vms
    key = ("rack-3", "power_kw", None)
    thr = LIMITS["power_kw"][0] - LIMITS["power_kw"][1]
    day = first_cross(lv[key] + ramp(d, 130, 0.1), thr, 150)
    lv[key] = lv[key] + ramp(d, 130, 0.1, stop=day + 2) - np.where(d >= day + 2, 12, 0)
    events.append((day, "rack-3", "power_kw",
                   "Power alarm on rack-3: GPU build-out exceeded circuit budget; nodes moved"))
    for rack in RACKS:
        lv[(rack, "temp_c", None)] = 21 + 0.15 * lv[(rack, "power_kw", None)]
    return lv, events


def generate(seed=7):
    rng = np.random.default_rng(seed)
    lv, ex_events = daily_levels()
    hours = pd.date_range(START, periods=N_DAYS * 24, freq="h")
    day_idx = np.repeat(np.arange(N_DAYS), 24)
    hod = hours.hour.values
    weekend = (hours.day_of_week.values >= 5)
    frames = []
    for (rack, res, tenant), level in lv.items():
        x = level[day_idx].copy()
        if res in ("power_kw", "network_gbps", "vm_count"):
            x *= np.where(weekend, 0.94, 1.02)                         # weekly seasonality
        if res in ("power_kw", "network_gbps", "temp_c"):
            x *= 1 + 0.04 * np.sin((hod - 9) / 24 * 2 * np.pi)       # daily cycle
        noise = {"temp_c": 0.015, "storage_tb": 0.004}.get(res, 0.03)
        x *= 1 + rng.normal(0, noise, len(x))
        for s_rack, s_res, s_ten, s_day, s_len, amt in SPIKES:
            if (s_rack, s_res, s_ten) == (rack, res, tenant):
                x += np.where((day_idx >= s_day) & (day_idx < s_day + s_len), amt, 0)
        keep = level[day_idx] > 0                                     # tenant-E absent before onboarding
        frames.append(pd.DataFrame({"timestamp": hours[keep], "rack": rack, "tenant": tenant,
                                    "resource": res, "value": np.round(x[keep], 3)}))
    telemetry = pd.concat(frames, ignore_index=True)

    limits = pd.DataFrame([(r, res, c, m) for r in RACKS for res, (c, m) in LIMITS.items()],
                          columns=["rack", "resource", "capacity", "safety_margin"])
    ev = [(START + pd.Timedelta(days=day), rack, res, "exhaustion", desc)
          for day, rack, res, desc in ex_events]
    ev += [(START + pd.Timedelta(days=sd), r, res, "transient_spike",
            f"Short {res} spike on {r} ({n} day(s)); resolved without intervention")
           for r, res, _, sd, n, _ in SPIKES]
    ev.append((START + pd.Timedelta(days=290), "rack-4", "storage_tb", "tenant_onboarding",
               "tenant-E onboarded on rack-4"))
    events = pd.DataFrame(ev, columns=["timestamp", "rack", "resource", "event_type",
                                       "description"]).sort_values("timestamp")
    return telemetry, limits, events


def write(out_dir=config.SYNTH_DIR):
    out_dir.mkdir(parents=True, exist_ok=True)
    t, l, e = generate()
    t.to_csv(out_dir / "telemetry.csv.gz", index=False)
    l.to_csv(out_dir / "limits.csv", index=False)
    e.to_csv(out_dir / "events.csv", index=False)
    return t, l, e


if __name__ == "__main__":
    t, l, e = write()
    print(t.shape, "\n", e.to_string())
