"""Convert Alibaba cluster-trace-v2018 machine_usage.tar.gz into AO-02 canonical telemetry
+ limits, as a separate data-centre record with ONE MODEL STEP = ONE REAL HOUR.

usage: python -I alibaba_to_canonical.py <machine_usage.tar.gz> <out dir>

The trace is 8 days long; AO-02 resamples to daily steps, so 8 days would give 8 points.
Each real hour is therefore written as one "day" on a synthetic calendar starting at
EPOCH (2000-01-01, deliberately not a real date). In this record: "day" = 1 hour,
"week" = 7 hours, horizon 180 "days" = 7.5 real days.

Real:      cpu %, memory %, disk-busy % (all 0-100, so capacity 100 is real), network
           traffic rate (derived from the cumulative normalised net_in/net_out counters)
           is computed but NOT written - there is no capacity to forecast it against.
Synthetic: racks (contiguous machine ids, 50 racks), tenants (machine id % 5), the
           hour->day relabelling, and the 10% safety margin.
"""
import json
import sys
import tarfile
from pathlib import Path

import pandas as pd

EPOCH = pd.Timestamp("2000-01-01")
N_RACKS = 50
TENANTS = [f"tenant-{c}" for c in "ABCDE"]
COLS = ["machine", "t", "cpu", "mem", "mem_gps", "mkpi", "net_in", "net_out", "disk_io"]
PCT = {"cpu": "cpu_pct", "mem": "mem_pct", "disk_io": "disk_io_pct"}
MARGIN = 10.0                    # alert at 90% (assumed)


def hourly(path):
    """Stream the CSV out of the tar.gz -> per (machine, hour): mean of the % metrics and
    max of the cumulative network counters."""
    sums, nets = [], []
    with tarfile.open(path, "r|gz") as tar:
        member = next(m for m in tar if m.name.endswith("machine_usage.csv"))
        for i, d in enumerate(pd.read_csv(tar.extractfile(member), header=None, names=COLS,
                                          usecols=["machine", "t", "cpu", "mem", "net_in",
                                                   "net_out", "disk_io"],
                                          chunksize=5_000_000)):
            d["hour"] = (d["t"] // 3600).astype(int)
            d.loc[~d["disk_io"].between(0, 100), "disk_io"] = None   # -1 / 101 = invalid
            for c in ("cpu", "mem"):
                d.loc[~d[c].between(0, 100), c] = None
            g = d.groupby(["machine", "hour"])
            sums.append(g[list(PCT)].agg(["sum", "count"]))
            nets.append(g[["net_in", "net_out"]].max())
            print(f"chunk {i}: {len(d):,} rows", flush=True)
    s = pd.concat(sums).groupby(level=[0, 1]).sum()
    pct = pd.DataFrame({c: s[(c, "sum")] / s[(c, "count")].where(s[(c, "count")] > 0)
                        for c in PCT})
    net = pd.concat(nets).groupby(level=[0, 1]).max()
    return pct, net


def main(src, out):
    pct, net = hourly(src)
    # network rate = growth of the cumulative counter per hour (normalised units / hour)
    net = net.sort_index()
    rate = (net.groupby(level=0).diff().clip(lower=0).sum(axis=1)).rename("network_rate")
    df = pct.join(rate, how="outer").reset_index()

    num = df["machine"].str.extract(r"(\d+)$")[0].astype(int)
    per_rack = -(-num.max() // N_RACKS)
    df["rack"] = "rack-" + ((num - 1) // per_rack).astype(str).str.zfill(2)
    df["tenant"] = [TENANTS[n % len(TENANTS)] for n in num]
    df["timestamp"] = EPOCH + pd.to_timedelta(df["hour"], unit="D")     # 1 hour -> 1 "day"

    rows = []
    # network_rate is NOT written: the trace gives no link capacity, and every series the
    # engine/app see must have a limit. (Rate is still computed above for inspection.)
    for col, res in PCT.items():
        v = df.dropna(subset=[col])
        n = v.groupby(["rack", "hour"])["machine"].transform("nunique")
        # each tenant's share of the rack MEAN, so rack total (= sum of tenants) = rack mean
        v = v.assign(value=v[col] / n)
        rows.append(v.groupby(["timestamp", "rack", "tenant"])["value"].sum().reset_index()
                    .assign(resource=res))
    tel = pd.concat(rows, ignore_index=True)[["timestamp", "rack", "tenant", "resource",
                                               "value"]]
    lim = pd.DataFrame([{"rack": "*", "resource": r, "capacity": 100.0,
                         "safety_margin": MARGIN} for r in PCT.values()])

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tel.to_csv(out / "telemetry.csv.gz", index=False)
    lim.to_csv(out / "limits.csv", index=False)
    (out / "record.json").write_text(json.dumps(       # tells the app 1 model day = 1 hour
        {"name": "Alibaba 2018 · 8 days, hourly", "step": "hour", "epoch": str(EPOCH.date())}))
    print(f"wrote {len(tel):,} rows, {tel.rack.nunique()} racks, {df.machine.nunique()} machines,"
          f" {df.hour.nunique()} hourly steps")
    rack_tot = tel.groupby(["timestamp", "rack", "resource"])["value"].sum()
    print(rack_tot.groupby(level="resource").describe().round(2).to_string())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
