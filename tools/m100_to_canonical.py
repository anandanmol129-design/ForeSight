"""Convert M100 ExaData raw parquet (per-node, ~20-40 s samples) into AO-02's canonical
hourly telemetry + an ASSUMED limits file.

usage: python -I m100_to_canonical.py <raw m100 dir> <out dir> <YY-MM> [<YY-MM> ...]

Real:      power, inlet temperature, network bytes, local-disk usage, disk size.
Synthetic: tenants (node -> tenant-{A..E} by node id), capacity limits (see LIMITS).
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

NODES_PER_RACK, N_COMPUTE = 20, 980          # gitlab.com/ecs-lab/exadata racks_spatial_distribution
TENANTS = [f"tenant-{c}" for c in "ABCDE"]
MIN_REPORTING = 0.8                          # drop rack-hours where <80% of nodes reported
METRICS = {"total_power": "ipmi_pub", "ambient": "ipmi_pub", "bytes_in": "ganglia_pub",
           "bytes_out": "ganglia_pub", "disk_free": "ganglia_pub", "disk_total": "ganglia_pub"}
# ASSUMED limits (not in the dataset):
LIMITS = {
    "power_kw": (20 * 2.2, 0.10),        # 20 nodes x 2.2 kW PSU rating (IBM AC922), 10% margin
    "temp_c": (32.0, 2.0),               # ASHRAE A1 allowable inlet max on the rack MEDIAN inlet, 2 degC margin
    "network_gbps": (20 * 10.0, 0.10),   # 10 Gbps per node assumed, 10% margin
}


def hourly_node_mean(path):
    """Stream the parquet in batches -> mean per (node, hour)."""
    parts = []
    for b in pq.ParquetFile(path).iter_batches(batch_size=5_000_000,
                                               columns=["timestamp", "value", "node"]):
        d = b.to_pandas()
        d["node"] = pd.to_numeric(d["node"], errors="coerce")
        d = d[d["node"] < N_COMPUTE].dropna()
        d["hour"] = d["timestamp"].dt.floor("h").dt.tz_localize(None)
        parts.append(d.groupby(["node", "hour"])["value"].agg(["sum", "count"]))
    s = pd.concat(parts).groupby(level=[0, 1]).sum()
    return (s["sum"] / s["count"]).rename("value").reset_index()


def main(raw, out, months):
    per = {m: [] for m in METRICS}
    for ym in months:
        for metric, plugin in METRICS.items():
            f = Path(raw) / f"year_month={ym}/plugin={plugin}/metric={metric}/a_0.parquet"
            if not f.exists():
                sys.exit(f"missing {f} - is month {ym} fully downloaded?")
            print("reading", ym, metric, flush=True)
            per[metric].append(hourly_node_mean(f))
    m = {k: pd.concat(v) for k, v in per.items()}

    node = m["total_power"]
    node["rack"] = "rack-" + (node["node"] // NODES_PER_RACK).astype(int).astype(str).str.zfill(2)
    rack_of = node.drop_duplicates("node").set_index("node")["rack"]
    tenant_of = pd.Series({n: TENANTS[int(n) % len(TENANTS)] for n in rack_of.index})

    def tag(df):
        return df.assign(rack=df["node"].map(rack_of), tenant=df["node"].map(tenant_of)).dropna()

    def enough(df):  # keep rack-hours where most nodes reported, so sums don't dip on gaps
        n = df.groupby(["rack", "hour"])["node"].transform("nunique")
        return df[n >= MIN_REPORTING * NODES_PER_RACK]

    rows = []
    p = enough(tag(node))
    rows.append(p.groupby(["hour", "rack"])["value"].sum().div(1000).reset_index()
                 .assign(tenant=None, resource="power_kw"))
    t = enough(tag(m["ambient"]))
    rows.append(t.groupby(["hour", "rack"])["value"].median().reset_index()   # typical inlet
                 .assign(tenant=None, resource="temp_c"))
    net = m["bytes_in"].merge(m["bytes_out"], on=["node", "hour"], suffixes=("_in", "_out"))
    net["value"] = (net["value_in"] + net["value_out"]) * 8 / 1e9
    rows.append(enough(tag(net)).groupby(["hour", "rack", "tenant"])["value"].sum()
                .reset_index().assign(resource="network_gbps"))
    disk = m["disk_free"].merge(m["disk_total"].groupby("node")["value"].median()
                                .rename("total").reset_index(), on="node")
    disk["value"] = (disk["total"] - disk["value"]) / 1000          # used, GB -> TB
    rows.append(enough(tag(disk)).groupby(["hour", "rack", "tenant"])["value"].sum()
                .reset_index().assign(resource="storage_tb"))
    tel = pd.concat(rows, ignore_index=True).rename(columns={"hour": "timestamp"})
    tel = tel[["timestamp", "rack", "tenant", "resource", "value"]]

    lim = [{"rack": "*", "resource": r, "capacity": c,
            "safety_margin": mg if r == "temp_c" else c * mg} for r, (c, mg) in LIMITS.items()]
    disk_tb = m["disk_total"].groupby("node")["value"].median().div(1000)
    for rack, cap in disk_tb.groupby(disk_tb.index.map(rack_of)).sum().items():
        lim.append({"rack": rack, "resource": "storage_tb", "capacity": round(cap, 3),
                    "safety_margin": round(0.1 * cap, 3)})            # real disk size, 10% margin

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tel.to_csv(out / "telemetry.csv.gz", index=False)
    pd.DataFrame(lim).to_csv(out / "limits.csv", index=False)
    (out / "record.json").write_text(json.dumps(
        {"name": f"M100 CINECA · {months[0]}..{months[-1]}", "step": "day"}))
    print(f"wrote {len(tel):,} rows, {tel.rack.nunique()} racks, "
          f"{tel.timestamp.min()} .. {tel.timestamp.max()}")
    print(tel.groupby("resource")["value"].describe().round(2).to_string())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
