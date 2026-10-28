"""Data adapter: maps whatever is in ./data/ onto the three canonical tables.

  telemetry: timestamp, rack, tenant (None for rack-level metrics), resource, value
  limits:    rack, resource, capacity, safety_margin (absolute units)
  events:    timestamp, rack, resource, event_type, description

All column names come from config.py. If ./data/ is empty we fall back to synthetic data.
Run `python -m pipeline.loader` to print an inspection of every file in ./data/.
"""
import hashlib

import pandas as pd

import config
from pipeline import synthetic

READERS = {".csv": pd.read_csv, ".gz": pd.read_csv, ".parquet": pd.read_parquet,
           ".json": pd.read_json, ".xlsx": pd.read_excel}
TELEMETRY_COLS = ["timestamp", "rack", "tenant", "resource", "value"]
LIMIT_COLS = ["rack", "resource", "capacity", "safety_margin"]
EVENT_COLS = ["timestamp", "rack", "resource", "event_type", "description"]


def data_files():
    if not config.DATA_DIR.exists():
        return []
    return sorted(p for p in config.DATA_DIR.rglob("*") if p.is_file() and p.suffix in READERS)


def read_file(path):
    return READERS[path.suffix](path)


def inspect():
    for p in data_files():
        df = read_file(p)
        print(f"\n=== {p.name}: {len(df):,} rows ===")
        print(df.dtypes.to_string())
        print(df.head().to_string())


def _find(source):
    matches = [p for p in data_files() if p.match(source["glob"])]
    if not matches:
        raise FileNotFoundError(f"No file in {config.DATA_DIR} matches {source['glob']!r} "
                                "- edit the glob in config.py")
    return pd.concat([read_file(p) for p in matches], ignore_index=True)


def _rename(df, mapping, optional=(), what=""):
    """Select + rename source columns to canonical names. A missing required column is a
    loud error naming the columns the file really has (so config.py is a one-line fix)."""
    out = pd.DataFrame(index=df.index)
    for canon, src in mapping.items():
        if src is not None and src in df.columns:
            out[canon] = df[src]
        elif canon in optional:
            out[canon] = None
        else:
            raise KeyError(f"{what}: column {src!r} (for {canon!r}) not found. "
                           f"File columns: {list(df.columns)} - fix config.py")
    return out


def _load_telemetry(src):
    raw, cols = _find(src), dict(src["columns"])
    if src.get("wide_value_columns"):                  # one column per metric -> long format
        ids = [c for c in (cols["timestamp"], cols["rack"], cols.get("tenant")) if c in raw]
        raw = raw.melt(id_vars=ids, value_vars=list(src["wide_value_columns"]),
                       var_name="__resource", value_name="__value")
        raw["__resource"] = raw["__resource"].map(src["wide_value_columns"])
        cols.update(resource="__resource", value="__value")
    elif src.get("resource"):                          # single-metric file
        raw["__resource"] = src["resource"]
        cols["resource"] = "__resource"
    return _rename(raw, cols, optional=("tenant",), what=f"telemetry {src['glob']}")


def load_real():
    tel = pd.concat([_load_telemetry(s) for s in config.TELEMETRY_SOURCES], ignore_index=True)

    lim = _rename(_find(config.LIMITS_SOURCE), config.LIMITS_SOURCE["columns"],
                  optional=("rack",), what="limits")
    if config.LIMITS_SOURCE["margin_is_fraction"]:
        lim["safety_margin"] = lim["safety_margin"] * lim["capacity"]
    try:
        ev = _rename(_find(config.EVENTS_SOURCE), config.EVENTS_SOURCE["columns"],
                     optional=("rack", "description"), what="events")
    except FileNotFoundError:
        ev = pd.DataFrame(columns=EVENT_COLS)
    return tel, lim, ev


def load_synthetic():
    files = [config.SYNTH_DIR / f for f in ("telemetry.csv.gz", "limits.csv", "events.csv")]
    if not all(f.exists() for f in files):
        synthetic.write()
    return tuple(pd.read_csv(f) for f in files)


def _normalise(tel, lim, ev):
    tel = tel.copy()
    tel["timestamp"] = pd.to_datetime(tel["timestamp"], utc=True).dt.tz_localize(None)
    tel["value"] = pd.to_numeric(tel["value"], errors="coerce")
    for df in (tel, lim, ev):
        df["rack"] = df["rack"].astype(str)
        df["resource"] = df["resource"].astype(str).replace(config.RESOURCE_ALIASES)
    tel["tenant"] = tel["tenant"].where(tel["tenant"].notna() & (tel["tenant"] != ""), None)
    rack_level = tel["resource"].isin(config.RACK_LEVEL_RESOURCES)
    if tel.loc[rack_level, "tenant"].notna().any():    # e.g. power repeated on every tenant row
        dedup = (tel[rack_level].groupby(["timestamp", "rack", "resource"], as_index=False)
                 ["value"].mean().assign(tenant=None))
        tel = pd.concat([tel[~rack_level], dedup], ignore_index=True)
    lim = _expand_limits(lim, tel["rack"].unique())
    ev = ev.copy()
    ev["timestamp"] = pd.to_datetime(ev["timestamp"], utc=True).dt.tz_localize(None)
    ev["event_type"] = ev["event_type"].astype(str).str.strip().str.lower()
    return (tel.dropna(subset=["timestamp", "value"])[TELEMETRY_COLS],
            lim[LIMIT_COLS].astype({"capacity": float, "safety_margin": float}),
            ev[EVENT_COLS].sort_values("timestamp"))


def _expand_limits(lim, racks):
    """A limit with no rack (or "*" / "all") applies to every rack; rack-specific rows win."""
    lim = lim.copy()
    wild = lim["rack"].isna() | lim["rack"].isin(["None", "nan", "*", "all", "ALL", ""])
    expanded = [lim[wild].assign(rack=rack) for rack in racks]
    out = pd.concat([lim[~wild]] + expanded, ignore_index=True)
    return out.drop_duplicates(["rack", "resource"], keep="first")


def load():
    """Returns (telemetry, limits, events, source) where source is 'real' or 'synthetic'."""
    if data_files():
        return *_normalise(*load_real()), "real"
    return *_normalise(*load_synthetic()), "synthetic"


def data_hash(df):
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()


if __name__ == "__main__":
    if data_files():
        inspect()
    t, l, e, s = load()
    print(f"\nsource={s} telemetry={len(t):,} rows, limits={len(l)}, events={len(e)}")
    print(t.groupby(["rack", "resource"]).size().to_string())
