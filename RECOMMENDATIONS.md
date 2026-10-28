# Recommendations: what to do about a forecast

The forecast says **what** runs out and **when**. The recommendation layer says **what to do
about it**: hardware upgrades, optimisations, inefficiencies worth checking. For each action it
also shows the **2nd-order effects**, such as more power draw and heat, or another rack losing
headroom.

> **Status: hardcoded prototype.** Rack specs, actions and their deltas are written by hand for
> the synthetic scenario (`pipeline/synthetic.py`). The *current* numbers come from the live
> forecast. On any other record the feature hides itself (`recommend.available()`). The section
> [Moving to real data](#moving-to-real-data) lists what has to change.

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
| Now weeks to safe limit | The engine's own P50 ETA (`eta["likely"] / 7`), so it matches the Overview |
| After level | now + Σ`level` |
| After capacity / safe limit | capacity + Σ`capacity`, with the same safety margin |
| After weeks to safe limit | Linear what-if: (safe limit − level) / (slope + Σ`slope`). Shown as `>25` past the horizon. |
| Peak | after + Σ`level` of the `temporary` changes |
| Verdict | **over**: at or above the safe limit. **tight**: < `WARNING_WEEKS` (8) to the safe limit, or within 5 % of capacity of it. **ok**: anything else. |

* `evaluate(results)` scores each action on its own.
* `combined(results, ids)` sums the deltas of the chosen actions before scoring. That is how
  interactions show up: R1 + R7 together leave rack-3 tight on both network and power.

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
