"""Hourly telemetry -> gap-filled daily series per (rack, resource) and (rack, resource, tenant)."""
import pandas as pd

import config

MAX_GAP_DAYS = 3   # interpolate gaps up to this long; longer gaps stay NaN


def _daily(df, resource):
    how = config.DAILY_AGG.get(resource, "mean")
    s = df.set_index("timestamp")["value"].resample("D").agg(how)
    return s.interpolate(limit=MAX_GAP_DAYS, limit_area="inside")


def build_series(telemetry):
    """Returns (rack_series, tenant_series): dicts of daily pd.Series.

    Rack totals for tenant-split resources are the sum of tenant daily series, so a
    resource is never double counted. Rack-level metrics (tenant is None) are used as-is.
    """
    rack_series, tenant_series = {}, {}
    has_tenant = telemetry["tenant"].notna()
    for (rack, res, ten), g in telemetry[has_tenant].groupby(["rack", "resource", "tenant"]):
        tenant_series[(rack, res, ten)] = _daily(g, res)
    for (rack, res), g in telemetry[~has_tenant].groupby(["rack", "resource"]):
        rack_series[(rack, res)] = _daily(g, res)
    for rack, res in {(r, s) for r, s, _ in tenant_series} - set(rack_series):
        parts = [s for k, s in tenant_series.items() if k[:2] == (rack, res)]
        rack_series[(rack, res)] = pd.concat(parts, axis=1).sum(axis=1, min_count=1)
    return dict(sorted(rack_series.items())), dict(sorted(tenant_series.items()))
