"""The one place that runs the pipeline. The app and the backtest both call this, so the
Validation tab measures exactly what operators see."""
import pandas as pd

import config
from pipeline import alerts, attribution, audit, confidence, exhaustion
from pipeline.forecast import forecast


def threshold(limits, rack, resource):
    row = limits[(limits.rack == rack) & (limits.resource == resource)]
    if row.empty:
        return None, None
    cap, margin = float(row.capacity.iloc[0]), float(row.safety_margin.iloc[0])
    return cap, cap - margin


def run_series(raw, thr, cutoff):
    """Forecast at cutoff-2, cutoff-1, cutoff (3 daily runs).
    Returns (latest fc, eta, status, P50 ETA of each run - logged so the alert is auditable)."""
    cutoff = pd.Timestamp(cutoff)
    fc, e, likely = None, None, []
    for back in range(config.SUSTAIN_RUNS - 1, -1, -1):
        fc = forecast(raw, cutoff - pd.Timedelta(days=back))
        e = exhaustion.eta(fc, thr)
        likely.append(e["likely"])
    fresh = fc is not None and fc["level_shift_at"] is not None
    return fc, e, alerts.classify(likely, fresh), likely


def run_operator(rack_series, tenant_series, limits, cutoff, run_id=None, log=True):
    """Full operator run over every rack x resource. Returns a list of result dicts."""
    run_id = run_id or audit.new_run_id()
    results, records = [], []
    for (rack, res), raw in rack_series.items():
        cap, thr = threshold(limits, rack, res)
        if thr is None:
            continue
        fc, e, status, history = run_series(raw, thr, cutoff)
        conf = confidence.score(fc, e, history) if status != "OK" else None
        tenants = {k[2]: s for k, s in tenant_series.items() if k[:2] == (rack, res)}
        # "significant" growth = at least 2% of capacity per quarter
        shares, top = attribution.attribute(fc, tenants, min_slope=0.02 * cap / 90)
        driver = (attribution.driver_text(fc, top) if tenants
                  else "n/a (rack-level metric)")
        results.append({"rack": rack, "resource": res, "capacity": cap, "threshold": thr,
                        "fc": fc, "eta": e, "status": status, "shares": shares,
                        "driver": driver, "top": top, "confidence": conf,
                        "text": exhaustion.describe(rack, res, e, config.HORIZON_DAYS),
                        "recommendation": alerts.recommendation(status)})
        records.append(audit.record(run_id, "operator", rack, res, fc, e, status, driver,
                                    sustain_history=history, confidence=conf))
    if log:
        audit.append(records)
    return results


def run_tenant_series(tenant_series, cutoff, viewer, run_id=None, log=True):
    """Forecast each (rack, resource, tenant) usage series - no capacity, no alerting."""
    run_id = run_id or audit.new_run_id()
    results, records = [], []
    for (rack, res, ten), raw in tenant_series.items():
        fc = forecast(raw, cutoff)
        results.append({"rack": rack, "resource": res, "tenant": ten, "fc": fc})
        records.append(audit.record(run_id, viewer, rack, res, fc, tenant=ten))
    if log:
        audit.append(records)
    return results


def run_tenant(tenant, tenant_series, cutoff, run_id=None, log=True):
    """Tenant view: `tenant_series` must ALREADY be filtered to this tenant's rows.
    No capacity, no rack totals, no other tenants - just their own usage trend."""
    assert all(k[2] == tenant for k in tenant_series), "tenant isolation violated"
    return run_tenant_series(tenant_series, cutoff, f"tenant:{tenant}", run_id, log)
