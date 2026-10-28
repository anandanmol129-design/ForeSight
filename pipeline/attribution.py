"""Which tenant drives the growth? Per-tenant slope over the rack forecast's fit window."""
import numpy as np
import pandas as pd

import config
from pipeline import forecast as fcmod


def attribute(rack_fc, tenant_series: dict, min_slope=0.0):
    """`tenant_series`: {tenant: daily Series} for ONE rack/resource (operator view only).

    Returns a list of {tenant, slope, share_pct} sorted by share, plus the top driver.
    Share = tenant slope / sum of positive tenant slopes (shrinking tenants get 0%).
    """
    if rack_fc is None or not tenant_series or rack_fc["slope"] <= min_slope:
        return [], None
    rows = []
    for tenant, s in tenant_series.items():
        win = fcmod.smooth(s.loc[:rack_fc["cutoff"]]).loc[rack_fc["fit_start"]:].dropna()
        slope = float(np.polyfit(np.arange(len(win)), win.values, 1)[0]) if len(win) >= 7 else 0.0
        rows.append({"tenant": tenant, "slope_per_day": slope})
    total = sum(max(r["slope_per_day"], 0) for r in rows)
    for r in rows:
        r["share_pct"] = round(100 * max(r["slope_per_day"], 0) / total, 1) if total > 0 else 0.0
    rows.sort(key=lambda r: -r["share_pct"])
    top = rows[0] if rows and rows[0]["share_pct"] > 0 else None
    return rows, top


def _when(ts):
    """A date, or "Day N HH:00" on an hourly record (1 model day = 1 real hour from epoch)."""
    if config.RECORD["step"] != "hour":
        return f"{ts:%d %b %Y}"
    h = (ts - pd.Timestamp(config.RECORD["epoch"])) // pd.Timedelta(days=1)
    return f"Day {h // 24 + 1} {h % 24:02d}:00"


def driver_text(rack_fc, top):
    if rack_fc is None or top is None:
        return "no significant growth"
    cps = [c for c in rack_fc["change_points"] if c <= rack_fc["fit_start"]]
    since = f" since {_when(cps[-1])}" if cps and cps[-1] == rack_fc["fit_start"] else ""
    return f"{top['tenant']} ({top['share_pct']:.0f}% of growth{since})"
