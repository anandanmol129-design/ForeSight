# AO-02 · Seeing trouble before the capacity runs out

A read-only early-warning tool for a multi-tenant data centre. For every rack × resource
it tells an operator **which** resource will run out, **when** (with a range), **why**
(which tenant is driving the growth, and since when), and **what to do**. Every forecast is
written to an append-only audit log, along with a hash of the data it was based on.

## Run it

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python selfcheck.py              # full pipeline + backtest + app smoke test (asserts)
venv/bin/streamlit run app.py             # dashboard
```

`selfcheck.py` also warms the backtest cache in `.cache/`. Without it, the app's first load
replays every day of history, which takes about 45 s.

## How it works (`pipeline/`)

| step | file | what |
|---|---|---|
| 0 | `loader.py`, `synthetic.py` | Maps raw files onto the canonical tables. Falls back to synthetic data if `./data/` is empty. |
| 1 | `clean.py` | Resamples hourly data to daily (mean, or max for `temp_c`) and fills gaps of 3 days or less. Builds series per (rack, resource) and per (rack, resource, tenant). Rack total = sum of its tenants. |
| 2 | `changepoints.py` | Runs `ruptures` PELT with a piecewise-**linear** cost on the 7-day rolling median. A steady ramp stays one segment; steps and slope changes become change points. |
| 3 | `forecast.py` | Fits a linear trend to the 7-day rolling median after the last change point. P10/P50/P90 bands = ±1.28·σ·√h. Horizon is 180 days. |
| 4 | `exhaustion.py` | Finds when each band first crosses capacity − safety margin: earliest = P90, likely = P50, latest = P10. |
| 5 | `alerts.py` | An alert needs the P50 crossing on 3 consecutive daily runs. Severity by lead time: CRITICAL < 2 wks, WARNING 2–8 wks, PLAN > 8 wks. Anything less is HOLD. |
| 6 | `attribution.py` | Computes each tenant's slope over the same fit window, its % share of growth, and the top driver "since <change point>". |
| 7 | `backtest.py` | Replays the pipeline **every day**, using only data up to that day. Reports lead time, false alarms, spikes that alerted, and MAPE. |
| 8 | `audit.py` | Writes `audit/forecast_log.jsonl` (live runs) and `audit/backtest_log.jsonl` (replays). |
| – | `engine.py` | The single entry point. The app and the backtest both call it, so the Validation tab measures exactly what operators see. |

## Results on the synthetic data (`selfcheck.py`)

* **3/3** historical exhaustion events caught. Mean lead time **11.2 weeks**, minimum 9.9.
  At the first alert, the predicted ETA was within ±0.3 weeks of the real date.
* **0** false alarms. **0/5** short spikes raised an alert.
* MAPE of the P50 path: **2.1 %** over the 30 days before each event, **2.9 %** over the last 30 days.
* Live state at the end of the data:
  * CRITICAL: rack-1 network (tenant-C)
  * WARNING: rack-4 storage (tenant-E, since onboarding)
  * PLAN: rack-2 storage

These numbers come from a generator I designed, so they show the method works, not that it
will score this well on the real data.

## Assumptions (and deviations from the brief)

1. **Data.** `./data/` was empty, so synthetic data is used. `pipeline/synthetic.py` writes
   it to `synthetic_data/`, never into `./data/`. It covers 4 racks, 5 tenants and 365 days
   of hourly data.
   * `storage_tb`, `network_gbps` and `vm_count` are reported per tenant.
   * `power_kw` and `temp_c` are rack-level, with tenant = null. They cannot be attributed
     to a tenant, and the app says so.
2. **Event dates** for the synthetic exhaustions are the day the noise-free level crosses
   capacity − margin. A mitigation drop follows a few days later.
3. **Change-point cost.** I used a piecewise-linear cost (`model="linear"`) instead of
   rbf or l2. With l2, a steady growth ramp gets chopped into many "mean shift" segments,
   and rbf is too slow to replay daily. The search covers the last 240 days on a fixed
   5-day grid, so the change points stay stable from one day to the next. If `ruptures`
   fails to import, a small numpy binary-segmentation fallback with the same cost is used.
4. **Brand-new regime.** If the newest change point is < 21 days old, the brief's
   "last 60 days" fallback is used, with one change: the fit includes a level-shift term at
   that change point. Otherwise a fresh step (e.g. a tenant onboarding) would show up as a
   huge slope. Until the regime is 21 days old, only CRITICAL may fire; PLAN and WARNING
   are held as HOLD. This removed every post-mitigation and post-onboarding false alarm.
5. **Band width.** σ is the robust (MAD) standard deviation of the *raw* daily values
   around the fitted trend. This counts weekly seasonality but ignores one-off spikes.
6. **Already over the limit.** If the smoothed level is already past capacity − margin,
   ETA = 0 and the status is CRITICAL (once sustained).
7. **Backtest definitions.**
   * Lead time = event date − first sustained alert in the 120 days before the event,
     using data strictly before the event.
   * False alarms are counted per alert *episode* (consecutive alert days), not per day.
     An episode is false if no exhaustion occurs between its start and its P10 ETA (or
     the horizon) + 14 days.
   * Episodes whose window extends past the end of the data are reported as *pending*,
     not counted.
   * Only event types listed in `EXHAUSTION_EVENT_TYPES` count. Events of type
     `transient_spike` are checked separately: they must **not** alert.
8. **Attribution.** A tenant's share = its slope / sum of positive tenant slopes. A rack
   whose growth is below 2 % of capacity per quarter shows "no significant growth" instead
   of a meaningless 100 % for noise.
9. **Tenant isolation.**
   * "View as" in the sidebar simulates login.
   * In tenant view, telemetry is filtered to that tenant's rows **before** any computation.
   * Tenants never see rack totals, capacity lines, events, other tenants, the
     validation tab or the audit log. All of these would leak other tenants' usage.
   * A tenant's change points are computed from their own series only.
   * Cached functions take the viewer as an argument, so cache entries cannot cross views.
10. **Audit.** The audit log is append-only. Each live entry also records the P50 ETA from each of the 3 runs behind the alert decision. A live forecast is logged once per
    (data version, cutoff), not on every Streamlit rerun. Backtest replays go to a separate
    file, tagged `run_type: backtest`.
11. **Read-only.** Nothing in the code writes anywhere except `audit/`, `.cache/` and
    `synthetic_data/`.

## When the real data arrives

1. Drop the files into `./data/`. CSV, CSV.GZ, Parquet, JSON and XLSX are read
   (`pipeline/loader.py:17`). The synthetic fallback switches off automatically.
2. Run `venv/bin/python -m pipeline.loader`. It prints the columns, dtypes, head and row
   count of every file, then what was loaded.
3. Edit **`config.py`** to match:
   * **Telemetry** — **lines 14–30**, `TELEMETRY_SOURCES`. This is a *list*, with one
     entry per file layout, so power, thermal, storage and so on can come from different
     files.
     * Line 16: file glob.
     * Lines 17–23: source column for each canonical column. Set `tenant` to `None` if
       there is no tenant column.
     * Line 26: `wide_value_columns`, for one column per metric, e.g.
       `{"inlet_temp": "temp_c", "used_tb": "storage_tb"}`.
     * Line 28: `resource`, for a single-metric file such as `power.csv` →
       `"resource": "power_kw"`.
   * **Limits** — **lines 31–36**: glob and columns.
     * If limits are per resource rather than per rack, set the `rack` column to `None`
       (or use `*` in the file). The limit then applies to every rack.
     * Set `margin_is_fraction=True` if the margin is a percentage of capacity.
   * **Events** — **lines 37–41**: glob and columns.
   * **Event types** — **line 43**: `EXHAUSTION_EVENT_TYPES`, the values meaning
     "capacity actually ran out". **The Validation tab depends on this.**
     * Line 45: `SPIKE_EVENT_TYPES`, the values for short spikes that resolved on their own.
     * Matching ignores case. If nothing matches, the Validation tab and `selfcheck.py`
       print the event types actually found.
   * **Resource names** — **line 47**: `RESOURCE_ALIASES`, mapping real names onto
     `power_kw`, `temp_c`, `storage_tb`, `network_gbps` and `vm_count`.
     * Line 55: `RACK_LEVEL_RESOURCES`. If these metrics are repeated on every tenant row,
       they are de-duplicated rather than summed.
     * Lines 56–57: `DAILY_AGG` and `UNITS`, for new resources.
4. A missing column fails loudly. The error names the column and lists the columns the
   file actually has. If timestamps need special parsing, edit `pipeline/loader.py:103`
   and `:116`.
5. Re-run `selfcheck.py`. On real data, the synthetic-scenario targets (an alert is
   active, 0 false alarms, every event caught) are printed as **WARN**, not FAIL. Read
   them as results, and don't tune the model just to make them pass.

The real-data path has been tested with a deliberately awkward fake dataset, and produced
the same results as the synthetic path. That dataset had:
* a wide per-tenant file, with rack power repeated on every tenant row
* renamed columns
* limits given per resource, with fractional margins
* capitalised event types and no description column

To force the synthetic data to regenerate, delete `synthetic_data/`. The backtest cache in
`.cache/` is keyed on the data and the model parameters, so it never goes stale.
