"""All tunables and column mappings live here. Edit this file when real data arrives."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).parent
SYNTH_DIR = ROOT / "synthetic_data"       # generated data (never written into DATA_DIR)
CACHE_DIR = ROOT / ".cache"
RECORDS_DIR = ROOT / "raw_data"           # data-centre records: raw_data/dc_*/ (see README there)


def use_record(path=None):
    """Point loading and auditing at one data-centre record (None = ./data, else synthetic).
    A record may hold record.json: {"name": ..., "step": "day" | "hour", "epoch": ...};
    step "hour" means each model day is one real hour, counted from epoch."""
    global DATA_DIR, AUDIT_DIR, RECORD
    DATA_DIR = ROOT / (path or "data")    # real data goes here; if empty we use synthetic data
    AUDIT_DIR = ROOT / "audit" / (Path(path).name if path else "")   # one audit log per record
    meta = DATA_DIR / "record.json"
    RECORD = {"name": Path(path).name if path else "default", "step": "day",
              "epoch": "2000-01-01", **(json.loads(meta.read_text()) if meta.exists() else {})}


def records():
    """{display name: path} of every record: the default (./data, else synthetic) first, then
    each raw_data/dc_*, then the active record if it lives anywhere else."""
    data = ROOT / "data"
    out = {"Local ./data" if data.exists() and any(data.iterdir()) else "Synthetic demo": None}
    seen = {data.resolve()}
    for d in (*sorted(RECORDS_DIR.glob("dc_*")), DATA_DIR):
        d = d.resolve()
        if d in seen or (d != DATA_DIR.resolve() and not any(d.glob("*telemetry*"))):
            continue                      # the active record is always listed
        seen.add(d)
        meta = d / "record.json"
        out[json.loads(meta.read_text()).get("name", d.name) if meta.exists() else d.name] = str(d)
    return out


def is_active(path):
    return (ROOT / (path or "data")).resolve() == DATA_DIR.resolve()


# AO02_DATA_DIR picks the starting record, e.g. AO02_DATA_DIR=raw_data/dc_m100
DEFAULT_RECORD = os.environ.get("AO02_DATA_DIR")
use_record(DEFAULT_RECORD)

# ---------------------------------------------------------------------------
# REAL DATA MAPPING  (edit these when the real files arrive; see README)
# Each entry: glob pattern inside DATA_DIR + {canonical column: source column}.
# ---------------------------------------------------------------------------
TELEMETRY_SOURCES = [   # one entry per telemetry file layout; add more for separate files
    {
        "glob": "*telemetry*",
        "columns": {                     # canonical -> source column name
            "timestamp": "timestamp",
            "rack": "rack",
            "tenant": "tenant",          # None if the file has no tenant column
            "resource": "resource",      # None if WIDE (below) or single-metric (see "resource")
            "value": "value",
        },
        # WIDE file (one column per metric): {source column: canonical resource}, e.g.
        # {"inlet_temp": "temp_c", "used_tb": "storage_tb"}
        "wide_value_columns": None,
        # Single-metric file (e.g. power.csv): the canonical resource name for every row
        "resource": None,
    },
]
LIMITS_SOURCE = {      # rack column missing / "*" / "all" = limit applies to every rack
    "glob": "*limit*",
    "columns": {"rack": "rack", "resource": "resource",
                "capacity": "capacity", "safety_margin": "safety_margin"},
    "margin_is_fraction": False,     # True if safety_margin is e.g. 0.1 meaning 10% of capacity
}
EVENTS_SOURCE = {
    "glob": "*event*",
    "columns": {"timestamp": "timestamp", "rack": "rack", "resource": "resource",
                "event_type": "event_type", "description": "description"},
}
# Event types (case-insensitive) that mean capacity actually ran out -> lead time / false alarms
EXHAUSTION_EVENT_TYPES = {"exhaustion", "capacity_exhausted", "capacity_breach", "outage"}
# Event types for short spikes that resolved on their own -> these must NOT raise an alert
SPIKE_EVENT_TYPES = {"transient_spike", "spike", "transient"}
# Map resource names found in the real data onto canonical names (identity if absent).
RESOURCE_ALIASES = {}

# ---------------------------------------------------------------------------
# RESOURCES
# ---------------------------------------------------------------------------
RESOURCES = ["power_kw", "temp_c", "storage_tb", "network_gbps", "vm_count", "licences"]
# Rack-level metrics: tenant is forced to None and duplicates per (timestamp, rack) are
# averaged, so a per-tenant file never multiplies rack power by the number of tenants.
RACK_LEVEL_RESOURCES = {"power_kw", "temp_c"}
DAILY_AGG = {"temp_c": "max"}            # everything else: mean
UNITS = {"power_kw": "kW", "temp_c": "°C", "storage_tb": "TB",
         "network_gbps": "Gbps", "vm_count": "VMs", "licences": "seats",
         "cpu_pct": "%", "mem_pct": "%", "disk_io_pct": "%"}

# ---------------------------------------------------------------------------
# MODEL PARAMETERS
# ---------------------------------------------------------------------------
ROLL_DAYS = 7               # rolling-median window (suppresses short spikes)
MIN_FIT_DAYS = 21           # need at least this much data after the last change point...
FALLBACK_FIT_DAYS = 60      # ...otherwise fit on the last 60 days
MAX_FIT_DAYS = 180          # never fit on more than this (old history rarely matters)
HORIZON_DAYS = 180
CP_MIN_SIZE = 14            # change-point segments at least 2 weeks long
CP_JUMP = 5
CP_PENALTY = 8.0            # multiplier on noise_var * log(n); higher = fewer change points
Z_80 = 1.2816               # P10/P90 = P50 -/+ Z_80 * sigma_h

SUSTAIN_RUNS = 3            # crossing must persist on 3 consecutive daily runs
CRITICAL_WEEKS = 2
WARNING_WEEKS = 8

# Backtest
BACKTEST_LOOKBACK_DAYS = 120
BACKTEST_STEP_DAYS = 1      # 1 = true daily replay; "3 consecutive runs" means 3 consecutive cutoffs
BACKTEST_START_DAY = 60     # first cutoff (days after data start) for the false-alarm scan
FALSE_ALARM_GRACE_DAYS = 14
MAPE_HOLDOUT_DAYS = 30

RECOMMENDATION = {
    "CRITICAL": "Urgent: act within days — stop new placements on this rack, migrate or "
                "throttle the driving workload, and escalate to the capacity owner.",
    "WARNING": "Rebalance: move workloads to racks with headroom and talk to the driving "
               "tenant about their growth plan.",
    "PLAN": "Plan expansion: add this to the next capacity review; no immediate action needed.",
    "HOLD": "Watch: a crossing appeared but has not persisted for 3 runs yet (possible spike).",
    "OK": "No action: no exhaustion forecast within the horizon.",
}
