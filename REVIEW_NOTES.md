# Review notes: real-data records, record switcher, hourly time display

Notes for testing and review agents. Nothing here is committed yet. The diff is mixed
with the owner's own UI work (`app.py` styling, `ui_fx.py`, `ui_story.py`, `assets/`,
`.streamlit/`). Only the hunks listed below belong to this change.

## What changed and why

### 1. Two real data-centre records (built only from `data_phone/`)
| Record | Source | Real resources | Synthetic / assumed |
|---|---|---|---|
| `raw_data/dc_m100` | CINECA Marconi100 ExaData, Zenodo 7588815 (CC-BY-4.0), months 20-08..20-11 | `power_kw` (rack sum of IPMI total_power), `temp_c` (rack **median** inlet, limit 32 °C − 2) | tenants = node % 5; power limit 44 kW; network 200 Gbps; storage = real disk size, 10% margins |
| `raw_data/dc_alibaba` | Alibaba cluster-trace-v2018 `machine_usage` (8 days) | `cpu_pct`, `mem_pct`, `disk_io_pct` (cap 100, alert at 90) | 50 racks from contiguous machine ids; tenants = machine id % 5; **1 real hour = 1 model day** from epoch 2000-01-01 |

- Each record folder holds `telemetry.csv.gz`, `limits.csv`, `README.md` (provenance and
  assumptions) and `record.json` (`name`, `step`: `day`|`hour`, `epoch`).
- `raw_data/` and `data_phone/` are **gitignored**. Reviewers need them on disk. Rebuild with:
  - `python -I tools/m100_to_canonical.py <extracted m100 dir> raw_data/dc_m100 20-08 20-09 20-10 20-11`
  - `python -I tools/alibaba_to_canonical.py data_phone/machine_usage.tar.gz raw_data/dc_alibaba`
- Known data limits (expected behaviour, not bugs):
  - M100 network is ~0, because only management Ethernet is monitored.
  - M100 storage is flat and only fully covered from 2020-09-29.
  - Alibaba network was dropped: its counters are cumulative and have no capacity.
  - There are no events files, so lead time can't be measured and every closed alert episode
    counts as a "false alarm".

### 2. Record selection (`config.py`)
- `use_record(path)` sets `DATA_DIR`, `AUDIT_DIR` (`audit/<record name>/`) and `RECORD`
  (from `record.json`).
- `records()` lists the default (`./data`, else "Synthetic demo"), each `raw_data/dc_*`,
  and the active record, which is always listed.
- `is_active(path)` reports whether `path` is the active record.
- `AO02_DATA_DIR` env var picks the starting record. When it's unset, behaviour is unchanged
  (synthetic fallback).
- `UNITS` gains `cpu_pct`, `mem_pct` and `disk_io_pct`.

### 3. Audit paths resolved at call time
- `pipeline/audit.py`: the `LOG` constant is replaced by `log_path()`.
- `pipeline/backtest.py`: `BACKTEST_LOG` is removed; the backtest writes to
  `config.AUDIT_DIR / "backtest_log.jsonl"`.
- `selfcheck.py` uses `audit.log_path()`.
- Reason: the app can now switch records while running.

### 4. Pipeline guard (`pipeline/forecast.py`)
- `forecast()` returns `None` when the cutoff is before a series starts. Before this, the
  backtest raised IndexError on short or late-starting real series.

### 5. App (`app.py`), owner-visible behaviour
- **"Data centre" selectbox** (sidebar/top bar):
  - Its `on_change` (`new_record`) clears the other session keys.
  - `config.use_record` is called **only if** the selection isn't already active. This keeps
    a test's redirected audit dir intact.
  - `load(data_dir)` takes the record path as its cache key.
- **Workloads:**
  - `WORKLOADS` gains CPU, Memory and Disk I/O.
  - `available_workloads(rows)` restricts the workload pickers and the story to what the
    record measures. Without it, Alibaba and M100 crashed on missing workloads.
- **Time units:** `HOURLY`, `UNIT_DAYS`, `U`/`U_LONG`, `STEP`/`RUNS` plus these helpers
  replace the hard-coded weeks and dates:
  - `dur()` and `fmt_eta()` replace `weeks()` and `fmt_weeks()`
  - `span()`
  - `when()`, which gives "Day N HH:00" on an hourly record
  - `eta_title()`, which replaces `r["text"]` as the detail chart title
  - `real_time(fig)`, which squeezes Plotly date axes by 24× on hourly records
- **Story:** its ticks are one per real day on hourly records, and it receives `smooth_win`
  (`ui_story.py`: the hint now reads it through a `.smooth-win` span).
- **Forecast-error KPI** shows "—" when there are no events (`mape_pre_event_pct` is None).
- **Story payload:** the smoothed series are `dropna()`'d. Real data has gaps, and NaN in the
  payload made the component's `JSON.parse` fail.
- **Audit tab** shows `audit.log_path()`, relative to the repo when possible.

### 5b. Presentation fixes (2026-10-29)
- **Story component (`ui_story.py`):** the key includes a hash of the rack set, so switching
  records rebuilds the 3D hall instead of reusing the old one. This fixes the `layoutMaps`
  TypeError after a switch. `layoutMaps` also skips any card with no rack position. With many
  racks (M100 has 49), the tenant maps fall back to a scrolling grid.
- **Colours:** `RES_COLOR` covers `cpu_pct`, `mem_pct` and `disk_io_pct`. Before this,
  every Alibaba line was white.
- **Wording when the limit is already crossed:**
  - `safe_in()` says "past safe limit".
  - `safe_eta()` drops the P10–P90 range then, so text no longer reads "(now–now h)".
  - `eta_u()` gives "now", not "now h" or "0.0 h", in the matrix cells, the Soonest ETA KPI
    and the HUD.
- **Hourly dates:**
  - `pipeline/attribution.py` `driver_text` formats "since …" with `_when()`. On Alibaba it
    showed "since 12 Jun 2000".
  - The Validation episode and spike tables go through `real_cols()`, which gives
    "Day N HH:00" and renames `*days*` columns to `*hours*`.
  - The Audit table still shows raw model dates on purpose: it is the log as written.
- **Records without an events log (M100, Alibaba):**
  - Validation shows an info box instead of the "fix EXHAUSTION_EVENT_TYPES" warning.
  - False alarms shows "—", because every closed episode would count as a false alarm.
  - Forecast error shows the last-30-step MAPE.
- **Tenant view:**
  - The default workload is the one whose total grows fastest across all racks. Before, it
    was the fastest per-rack grower, which picked M100's near-zero network.
  - Forecasts, bands and "Projected" are clipped at 0, so usage is never drawn below zero.

### 6. Tools (`tools/`)
- `m100_to_canonical.py`, `alibaba_to_canonical.py`: the converters above. They stream large
  inputs and write `record.json`.
- `run_selfcheck_on.py <data dir> <scratch dir>`: runs `selfcheck.py` against a record, with
  audit and cache redirected to scratch.

## How it was tested

**Never run `selfcheck.py` directly against a record during review.** Use
`tools/run_selfcheck_on.py`, so the real `audit/` is never written. Snapshot `audit/` with
md5sum before and after a run.

```bash
S=$(mktemp -d); mkdir -p "$S/empty"
find audit -type f -exec md5sum {} + | sort > "$S/before"
venv/bin/python tools/run_selfcheck_on.py "$S/empty"            "$S/synth"    # synthetic fallback
venv/bin/python tools/run_selfcheck_on.py raw_data/dc_m100      "$S/m100"
venv/bin/python tools/run_selfcheck_on.py raw_data/dc_alibaba   "$S/alibaba"
find audit -type f -exec md5sum {} + | sort | diff - "$S/before"   # expect no diff (unless the live app wrote)
```

Results (2026-10-28): **ALL PASSED** for all three, including AppTest (operator view, tenant
view, and no tenant leakage).

| Record | Live alerts | Backtest | Notes |
|---|---|---|---|
| synthetic | as before | lead 11.2 wks, 0 false alarms, MAPE 2.11 / 2.92 % | unchanged from README |
| M100 | 7 WARNING + 2 PLAN (temp_c), no power alerts (~12 kW vs 44 kW) | 75 episodes, 0 false alarms, MAPE last 30 days 20.95 % | no events, so no lead time |
| Alibaba | 50 CRITICAL mem_pct (fleet sits at ~88 % vs the 90 % alert line), CPU HOLD/PLAN | 195 episodes, 35 "false alarms", MAPE 19.88 % | CPU has a 24-hour cycle; linear fit partly extrapolates it |

Extra checks:
- `config`: the env unset/m100/alibaba each resolve to the right `DATA_DIR`, `AUDIT_DIR`,
  `RECORD` and row counts (390,840 / 835,446 / 141,000).
- Story payload is strict JSON: AppTest with `ui_story.story` monkeypatched to
  `json.dumps(payload, allow_nan=False)` gives no exceptions for M100 (284 KB) or Alibaba
  (318 KB).

UI pass (2026-10-29): headless Chromium driven over CDP against a scratch copy of the repo,
so the real `audit/` is untouched. The pass covers all 3 records × 5 operator tabs, the story
scroll and the tenant view. There were no JS exceptions, no console errors and no
`stException`. All three self-checks still pass after the fixes.

## Incidents a reviewer should know about
- **Audit pollution, fixed:**
  - An early test passed an empty scratch dir. It wasn't listed in `records()`, so the app
    switched to the default record and wrote into the real `audit/forecast_log.jsonl`.
  - The file was cut back to its pre-test 2,183-line prefix (md5 verified).
  - The cause was fixed: the active record is always listed, and the app switches only on a
    real selection change.
  - Lines the owner's live app wrote between 14:07 and 14:08 UTC were lost with it.
- **Live app:** the owner runs the app on :8502 (other sessions have used :8501/:8503). Any
  of these writes to the real `audit/` whenever someone views it, e.g. the synthetic entry at
  04:16 IST on 2026-10-29. New files in `audit/dc_m100/` and
  `audit/dc_alibaba/` come from that, not from the tests.

## Suggested review focus
1. **Concurrency:** `config` holds the active record globally for the whole process. Two
   sessions on different records at the same instant can race. This is acceptable for the
   prototype; confirm that's an acceptable trade-off.
2. **`real_time()`:** remaps traces, shapes, annotations and the axis range. Check any new
   chart for x values on other refs (`x domain` / `paper` are skipped on purpose).
3. **Hourly labels:** the Validation and Audit tables still show model dates (2000-…), and a
   sidebar caption explains this. The backtest's internal `lead_weeks` / "wks" strings are
   not unit-aware. Alibaba has no events, so they're unused today.
4. **Alibaba thresholds:** the 90 % memory alert line is an assumption and drives every
   CRITICAL.
5. **Gaps elsewhere:** other places that could receive NaN from real data with gaps.
   `trend_chart` / `chart` are fine (Plotly draws gaps). Any new JSON payload must drop NaN.
