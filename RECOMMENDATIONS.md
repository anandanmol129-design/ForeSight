# Recommendations: what to do about a forecast

The forecast says **what** runs out and **when**. The recommendation layer says **what to do
about it**: hardware upgrades, optimisations, inefficiencies worth checking. For each action it
also shows the **2nd-order effects**, such as more power draw and heat, or another rack losing
headroom.

> **Status:** the synthetic demo keeps its 8 hand-written actions. **Real records (M100,
> Alibaba) now get generated actions**: rules over the live forecast, the rack-1 profile for
> specs and upgrade sizes, and coefficients fitted from telemetry where the fit is good enough.
> See [Real data: what is implemented](#real-data-what-is-implemented). The original plan is
> kept below it under [Moving to real data](#moving-to-real-data).

## Where it shows up

| Place | What |
|---|---|
| **Overview → 02 Recommended plan** (under the main chart) | Left: plain bullet points for the selected rack, grouped as *Upgrade*, *Optimise*, *Check for inefficiency* and *2nd-order effects*. Right: one horizontal bar per affected rack × resource, coloured by resource. Grey = now, coloured = after, as % of the safe limit. A label on the right shows weeks to the safe limit, now → after, coloured green / amber / red. |
| ↳ *Apply* pills + *Combine* toggle | Off: one action at a time. On: several actions together. Bars and bullets update. |
| **Recommendations tab** | Rack specs table; one card per action (why, cost, lead time, the alert that triggered it, a before/after arrow chart, the other risks, and a *Details* table); a **Combined plan** with a rack power chart and a warning for anything left tight. |

## Files

| File | What |
|---|---|
| `pipeline/recommend.py` | All the logic and the hardcoded data: `RACK_SPECS`, `RECOMMENDATIONS`, `SUMMARY`, the coefficients `KW_PER_VM` and `C_PER_KW`, and the functions `evaluate()`, `combined()`, `touching()` and `available()`. |
| `app.py` | UI only: the Overview plan panel, `plan_bars()`, `effect_chart()`, `side_text()`, `effect_table` and the Recommendations tab. |
| `assets/style.css` | Selected-state styling for the *Apply* pills (end of file). |

## The recommendation record

Each entry of `RECOMMENDATIONS` is one action:

```python
{"id": "R2", "kind": "Hardware upgrade", "priority": "Next 4 weeks",
 "trigger": ("rack-1", "network_gbps"),          # alert it answers, or None
 "title": "...", "why": "...", "cost": "...", "lead": "...",
 "changes": [                                     # what it does to the telemetry
     {"rack": "rack-1", "resource": "network_gbps", "order": 1, "capacity": +100},
     {"rack": "rack-1", "resource": "power_kw",     "order": 2, "level": +0.35}],
 "risks": ["Oversubscription moves to the spine ...", "..."]}   # effects that aren't numbers
```

* `order` 1 is the intended effect. `order` 2 is a side effect.
* A change can carry any of these:
  * `level`: a one-off step in usage.
  * `slope`: a change in growth, in units per **week**.
  * `capacity`: a change in capacity; the safe limit moves with it, and the margin stays the same.
  * `temporary: True`: the change happens only while the work runs, e.g. rebalance traffic. It shows as a *peak* and is left out of the steady state.
* **Heat is never written by hand.** Every power change automatically gets a `temp_c` row of `C_PER_KW × ΔkW`. That makes power → heat an explicit 2nd-order chain.
* `SUMMARY` maps each id to the group and the one-line bullet used on the Overview.

The 8 actions in the synthetic scenario:

| Id | Group | Action | Main 2nd-order effect |
|---|---|---|---|
| R1 | Optimise | Move half of tenant-C off rack-1 to rack-3 (stopgap) | rack-3 network +21 Gbps, 15.3 wks to its safe limit |
| R2 | Upgrade | rack-1 ToR 2×50 → 2×100 GbE | +0.35 kW, bottleneck moves to the spine |
| R3 | Upgrade | rack-4 + 1 JBOD shelf (+250 TB usable) | +0.5 kW, +8 Gbps while rebalancing, longer disk rebuilds |
| R4 | Optimise | tenant-E compression + 30-day snapshot cap | +0.3 kW of compression CPU, fewer restore points |
| R5 | Optimise | Move 70 idle licence seats to rack-4 | rack-1 and rack-3 lose seat headroom |
| R6 | Optimise | Archive cold tenant-C data on rack-2 | +3 Gbps during the move, slower reads from the archive |
| R7 | Check for inefficiency | ≈37 % of the power budget is unused: put 4 GPU nodes on rack-3 | rack-3 power 35.5 / 36 kW when combined with R1 |
| R8 | Check for inefficiency | Storage grows with no tenant growth behind it (orphaned snapshots and logs) | Old restore points are lost |

## How the numbers are computed

For each rack × resource a change touches (`_row()`):

| Value | Source |
|---|---|
| Now level, capacity, margin | Live forecast result (`fc["last_level"]`, `capacity`, `threshold`) |
| Now weeks to safe limit | The engine's own P50 ETA (`eta["likely"] / unit_days`: weeks, or hours on an hourly record), so it matches the Overview |
| After level | now + Σ`level` |
| After capacity / safe limit | capacity + Σ`capacity`, with the same safety margin |
| After weeks to safe limit | Linear what-if: (safe limit − level) / (slope + Σ`slope`). Shown as `>25` past the horizon. |
| Peak | after + Σ`level` of the `temporary` changes |
| Verdict | **over**: at or above the safe limit. **tight**: < `WARNING_WEEKS` (8) to the safe limit, or within 5 % of capacity of it. **ok**: anything else. |

* `evaluate(results)` scores each action on its own.
* `combined(results, ids)` sums the deltas of the chosen actions before scoring. That is how
  interactions show up: R1 + R7 together leave rack-3 tight on both network and power.

## Real data: what is implemented

| Doc step | Done in `pipeline/recommend.py` | Notes |
|---|---|---|
| 1. Rack specs | `spec_for(rack)` falls back to the rack-1 (Gen-2) profile; `spec_table()` shows it as one column, with the Power / Network / Cooling / Storage lines rewritten to this record's `limits.csv` values and rows for resources the record doesn't measure dropped | Upgrade sizes come from the profiles: ToR doubles network, a shelf adds min(250 TB, rack capacity), GPU node 2.5 kW, host RAM 512 → 768 GB (the Gen-3 size) |
| 2. Coefficients | `fit(rack_series)`: per-rack straight line, median slope and R² over racks; used only when median R² ≥ 0.3 (`MIN_R2`) | Logged once per data hash as `run_type: coefficient_fit` in the record's audit log. **M100:** C_PER_KW fit 0.163 °C/kW but R² 0.057 → keeps 0.15 (inlet temperature follows the room, not the rack). KW_PER_VM: no record has `vm_count` → 0.03. |
| 3. Rules | `generate(results, coefs, step_days)` runs `RULES` and numbers the output R1… most urgent first | Same record shape as the hand-written list, so `evaluate`, `combined` and `touching` (now taking the list) and the whole UI are unchanged |
| 4.1 Switch on | `available(recs)` is true when the list is non-empty; `uncovered()` lists alerting pairs no action touches, shown in the tab caption | Operator only, as before (`plan_recs()` in `app.py` is called from the operator branch) |
| 4.3 Backtest hit rate | **Not done yet** | |
| 4.5 Hourly records | Weeks to safe limit are reported in the record's display unit (`unit_days`): hours on Alibaba; ToR lead time is compared in real days (`step_days`) | |

### Rules

Each rule caps itself at `MAX_RACKS` (4) racks, tightest first, and says how many more have the
same alert. Deltas that are not measured are labelled *assumption* or *guess* in the row note.

| Rule | When | Emits |
|---|---|---|
| `_rule_cooling` (new) | `temp_c` alert | **Check for inefficiency:** cooling/airflow check on every hot rack (drift stops, an assumption), naming racks where power is flat. **Upgrade:** rear-door heat exchangers on the 4 most urgent (−2 °C assumption, +0.4 kW fans). **Optimise:** move 25 % of the load from the hottest racks to the coolest, so the gain is only `C_PER_KW` × kW |
| `_rule_memory` (new) | `mem_pct` alert | **Upgrade:** host RAM 512 → 768 GB on the 4 tightest (use −⅓). **Check for inefficiency:** memory full while CPU < 50 % → right-size memory requests (−5 points, a guess) |
| `_rule_tor` | Network CRITICAL/WARNING with ETA < 35 days + 2 weeks | R2-type ToR upgrade |
| `_rule_storage` | Storage alert | R3-type shelf and R4-type compression + lifecycle |
| `_rule_licences` | Licence alert, other pools < 70 % used | R5-type seat reassignment |
| `_rule_rebalance` | Any other tenant-split resource alerting with positive slope | R1-type: move half of the top tenant (≥ 50 % share), else steer half of the new growth, to an OK rack with ≥ 5 % headroom, OK inlet temperature and no CRITICAL/WARNING on any resource (new load needs memory and power too) |
| `_rule_stranded_power` | Every rack under 70 % of its power budget | R7-type: GPU nodes on the rack with the most thermal headroom |
| `_rule_creep` | Rack total grows ≥ 2 % of capacity per quarter, no tenant does | R8-type orphan check |

### What it produces today

| Record | Actions |
|---|---|
| **M100** (9 `temp_c` alerts) | R1 rear-door heat exchangers on rack-02/00/11/13 · R2 shift load to the coolest racks (≈0.6 °C gain, shown as weak) · R3 cooling check on all 9 · R4 stranded power (31 % of budget used) → GPU nodes on rack-31. No alert is left uncovered. |
| **Alibaba** (`mem_pct` CRITICAL on all 50 racks) | R1 RAM upgrade on the 4 fullest racks · R2 memory-full/CPU-idle check on all 50. Priorities read *Now*, because the whole hourly horizon is days away. With every rack over 90 % memory, no rack can take moved load. So the 3 CPU PLAN racks stay uncovered (the caption says so), and the combined plan leaves 46 racks tight. |

## Moving to real data

The recommendation layer has no model to train like the forecaster. It has three parts, and
each one changes differently for real data:

| Part | Synthetic (now) | Real data |
|---|---|---|
| Rack specs | Hand-written `RACK_SPECS` | **Keep the same specs** (below) |
| Coefficients (`KW_PER_VM`, `C_PER_KW`) | Copied from the generator | **Fit** them from the record's telemetry |
| Actions and deltas | A hand-written list of 8 | **Generate** them from rules over the forecast results |

### 1. Rack specs: use the synthetic specs

Use the **same specs as the synthetic racks** for every real rack. The real records have
different rack names (`rack-00` … `rack-48` in M100, `rack-00` … `rack-49` in Alibaba), so
map each real rack onto one of the existing profiles instead of writing new ones:

* Default: every rack gets the **rack-1 profile**, i.e. Gen-2: 16 × 2U hosts, 400 VM slots, 500 TB, ToR 2 × 50 GbE, 2 × 32 A PDUs / 40 kW, air cooling, 250 seats.
* GPU racks or newer racks, if the record says so: the **rack-3** profile (GPU-ready) or the **rack-4** profile (Gen-3).
* Suggested change: replace the `RACK_SPECS[rack]` lookups with a `spec_for(rack)` that falls back to `RACK_SPECS["rack-1"]`.

The upgrade sizes are part of the spec and stay the same too: ToR 50 → 100 GbE doubles
network, a JBOD shelf adds 250 TB usable, a GPU node is 2.5 kW.

**Capacity comes from `limits.csv`, not from the spec text.** The forecast and the verdicts
use the record's limits. Those differ from the synthetic spec text: M100 has a 44 kW power
budget, a 32 °C temperature limit and 200 Gbps network. Either keep the specs as the
descriptive profile and the source of upgrade sizes, or edit the spec text to match each
record's limits.

### 2. Fit the coefficients from telemetry

Both coefficients are slopes of a straight-line fit on daily values per rack. Fit them with
`numpy.polyfit(x, y, 1)` on the rack series from `clean.build_series()`:

| Coefficient | Fit | Available in |
|---|---|---|
| `C_PER_KW` (°C per kW) | `temp_c` against `power_kw` | M100 (has both) |
| `KW_PER_VM` (kW per VM) | `power_kw` against `vm_count` | No real record yet. Keep the synthetic 0.03 until one has both. |

* Use the median over racks so one odd rack doesn't skew it.
* Keep the synthetic value when R² < 0.3; a weak fit is worse than a sensible default.
* Write the fitted value and its R² to the audit log, the same way forecasts are logged.

### 3. Generate the actions from rules

Replace the hand-written list with functions that read the forecast results (`status`,
`eta`, `top` driver, `shares`, `fc["slope"]`) and output the same record shape. Each
existing action becomes a rule:

| Rule (when) | Emits | Deltas from |
|---|---|---|
| Network CRITICAL/WARNING and one tenant drives most of the growth | R1-type rebalance to the rack with the most headroom on that resource | `level` = tenant's share × level, `slope` = tenant's slope; the receiving rack gets the opposite sign |
| Network ETA < upgrade lead time + margin | R2-type ToR upgrade | `capacity` = spec uplink size, `level` power = spec switch draw |
| Storage WARNING/PLAN | R3-type shelf (hardware) **and** R4/R6-type compression / tiering (optimise) | spec shelf size; tiering share = assumption until real access logs exist |
| Pooled resource (licences) PLAN on one rack and < 70 % used on others | R5-type reassignment | Move idle units so no donor drops below its safe limit + 10 % |
| Rack mean power < 70 % of budget on every rack | R7-type stranded-power note | spec GPU node draw |
| Positive slope but "no significant growth" from any tenant | R8-type creep check | Slope from the forecast; level = a guess, flag it as one |
| CPU / memory / disk I/O (Alibaba) | Not covered yet; needs new profiles (host CPU and RAM, disk type) | — |

* `trigger` = the result that fired the rule.
* `priority` comes from the alert severity: CRITICAL → *Now*, WARNING → *Next 2–4 weeks*, PLAN → *Next quarter*.

### 4. Switch it on and check it

1. `available(source)` currently returns `source == "synthetic"`. Make it true for real records once the rules exist.
2. Keep tenant isolation: the plan uses rack totals and other tenants, so it stays **operator-only**, like it is now.
3. Check it on history: at each past cutoff, apply the recommended action. Then check whether the *after* forecast would have stayed below the safe limit at the real exhaustion date. That gives a hit rate per rule, in the same spirit as `backtest.py`.
4. Run `selfcheck.py` on each record: `AO02_DATA_DIR=raw_data/dc_m100 venv/bin/python selfcheck.py`.
5. Hourly records (Alibaba): slopes in the records are per **week** and the ETAs are in model days. Convert with `UNIT_DAYS` the same way the rest of the app does.
