"""Actionable recommendations on top of the forecast: hardware upgrades, inefficiencies and
rebalancing, each with its 2nd-order effects (power draw, heat, other racks' headroom).

HARDCODED for the synthetic scenario (pipeline/synthetic.py): rack specs, actions and their
deltas are written by hand. The *before* numbers and the *after* ETAs are computed from the
live forecast, so the plan stays consistent with what the dashboard shows. Real data will
need these derived per record instead (see available()).
"""
import config

KW_PER_VM = 0.03        # synthetic rack model: power_kw = 18 + 0.03 * vm_count
C_PER_KW = 0.15         # synthetic thermal model: temp_c = 21 + 0.15 * power_kw

RACK_SPECS = {
    "rack-1": {"Generation": "Gen-2 (2022)", "Form factor": "42U, 16 × 2U hosts",
               "Compute": "2 × 32-core, 512 GB RAM per host · 400 VM slots",
               "Storage": "500 TB usable (NVMe cache + 12 TB HDD, 3× replica)",
               "Network": "ToR 2 × 50 GbE uplinks = 100 Gbps",
               "Power": "2 × 32 A 3-phase PDUs (A/B) · 40 kW circuit budget",
               "Cooling": "Air, hot-aisle containment · 35 °C inlet limit",
               "Licences": "250-seat per-host pool"},
    "rack-2": {"Generation": "Gen-2 (2022)", "Form factor": "42U, 16 × 2U hosts",
               "Compute": "2 × 32-core, 512 GB RAM per host · 400 VM slots",
               "Storage": "500 TB usable (NVMe cache + 12 TB HDD, 3× replica)",
               "Network": "ToR 2 × 50 GbE uplinks = 100 Gbps",
               "Power": "2 × 32 A 3-phase PDUs (A/B) · 40 kW circuit budget",
               "Cooling": "Air, hot-aisle containment · 35 °C inlet limit",
               "Licences": "250-seat per-host pool"},
    "rack-3": {"Generation": "Gen-3 GPU-ready (2024)",
               "Form factor": "42U, 14 × 2U hosts · 8U free (GPU nodes moved out Jun 2026)",
               "Compute": "2 × 48-core, 768 GB RAM per host · 400 VM slots",
               "Storage": "500 TB usable (all-flash, erasure-coded 8+3)",
               "Network": "ToR 2 × 50 GbE uplinks = 100 Gbps (100G-capable ports)",
               "Power": "2 × 63 A 3-phase PDUs (A/B) · 40 kW circuit budget",
               "Cooling": "Air + rear-door heat exchanger · 35 °C inlet limit",
               "Licences": "250-seat per-host pool"},
    "rack-4": {"Generation": "Gen-3 (2024)", "Form factor": "42U, 14 × 2U hosts · 4U free",
               "Compute": "2 × 48-core, 768 GB RAM per host · 400 VM slots",
               "Storage": "500 TB usable (12 TB HDD + NVMe, erasure-coded 8+3)",
               "Network": "ToR 2 × 50 GbE uplinks = 100 Gbps (100G-capable ports)",
               "Power": "2 × 32 A 3-phase PDUs (A/B) · 40 kW circuit budget",
               "Cooling": "Air, hot-aisle containment · 35 °C inlet limit",
               "Licences": "250-seat per-host pool"},
}

# changes: {rack, resource, order (1 = intended effect, 2 = side effect), and any of
#   level (Δ units), slope (Δ units/week), capacity (Δ units), temporary (True = during the
#   work only, not part of the steady state), note}.
# temp_c rows are never written by hand: they are derived from the power rows (C_PER_KW).
RECOMMENDATIONS = [
    {"id": "R1", "kind": "Rebalance", "priority": "Now", "trigger": ("rack-1", "network_gbps"),
     "title": "Move half of tenant-C's network-heavy services from rack-1 to rack-3",
     "why": "rack-1 network is at 88 % and tenant-C drives 100 % of its growth. A ToR "
            "upgrade (R2) takes longer than the time left, so this is the stopgap.",
     "cost": "No capex · 1–2 days of migration work", "lead": "Days",
     "changes": [
         {"rack": "rack-1", "resource": "network_gbps", "order": 1, "level": -21, "slope": -0.85,
          "note": "tenant-C traffic leaves the rack"},
         {"rack": "rack-3", "resource": "network_gbps", "order": 2, "level": +21, "slope": +0.85,
          "note": "rack-3 inherits the traffic and its growth"},
         {"rack": "rack-1", "resource": "vm_count", "order": 2, "level": -30, "note": "30 VMs move"},
         {"rack": "rack-3", "resource": "vm_count", "order": 2, "level": +30, "note": "30 VMs arrive"},
         {"rack": "rack-1", "resource": "power_kw", "order": 2, "level": -30 * KW_PER_VM,
          "note": f"{KW_PER_VM} kW per VM"},
         {"rack": "rack-3", "resource": "power_kw", "order": 2, "level": +30 * KW_PER_VM,
          "note": f"{KW_PER_VM} kW per VM"}],
     "risks": ["East-west traffic between tenant-C's split services now crosses the spine "
               "(≈ +5 Gbps spine load).",
               "Moving the growth also moves the problem: rack-3 network now has an ETA "
               "of its own, so R2 still has to happen.",
               "tenant-C will see higher latency during the live migration. Agree a window with them."]},

    {"id": "R2", "kind": "Hardware upgrade", "priority": "Next 4 weeks",
     "trigger": ("rack-1", "network_gbps"),
     "title": "Upgrade rack-1 ToR uplinks from 2 × 50 GbE to 2 × 100 GbE (QSFP28)",
     "why": "This is the permanent fix for rack-1 network. Capacity doubles, which absorbs "
            "tenant-C's 1.7 Gbps/week growth for years.",
     "cost": "≈ $18k (switch + optics) · 30 min failover window", "lead": "3–5 weeks (optics)",
     "changes": [
         {"rack": "rack-1", "resource": "network_gbps", "order": 1, "capacity": +100,
          "note": "100 → 200 Gbps"},
         {"rack": "rack-1", "resource": "power_kw", "order": 2, "level": +0.35,
          "note": "100G switch + 4 optics"}],
     "risks": ["The oversubscription bottleneck moves up to the spine. Two more 100G spine "
               "ports are used, which leaves 2 free.",
               "During the A/B failover window there is a single uplink, with no redundancy."]},

    {"id": "R3", "kind": "Hardware upgrade", "priority": "Next 2 weeks",
     "trigger": ("rack-4", "storage_tb"),
     "title": "Add one 2U JBOD shelf (24 × 16 TB HDD) to rack-4's storage cluster",
     "why": "rack-4 storage hits its safe limit in about 5 weeks, and tenant-E's growth since "
            "onboarding drives 85 % of that. The shelf fits in the free 4U and its lead "
            "time is shorter than the ETA.",
     "cost": "≈ $14k", "lead": "2–3 weeks",
     "changes": [
         {"rack": "rack-4", "resource": "storage_tb", "order": 1, "capacity": +250,
          "note": "384 TB raw → ≈250 TB usable (EC 8+3, 10 % reserve)"},
         {"rack": "rack-4", "resource": "power_kw", "order": 2, "level": +0.5,
          "note": "24 spinning drives + expander"},
         {"rack": "rack-4", "resource": "network_gbps", "order": 2, "level": +8, "temporary": True,
          "note": "cluster rebalance for ≈1 week"}],
     "risks": ["Rebuilding a failed 16 TB HDD takes 1–2 days, so there is a longer window "
               "of reduced redundancy.",
               "Backup software is licensed per TB, so backup cost grows with used capacity.",
               "It uses 2U of rack-4's last 4U free."]},

    {"id": "R4", "kind": "Inefficiency", "priority": "Next 2 weeks",
     "trigger": ("rack-4", "storage_tb"),
     "title": "tenant-E: enable inline compression and cap snapshot retention at 30 days",
     "why": "tenant-E adds ≈3.5 TB/week, about 17 times the per-tenant baseline on other racks. "
            "That pattern usually means uncompressed logs or snapshots that are never pruned.",
     "cost": "No capex · needs tenant-E's agreement", "lead": "1 week",
     "changes": [
         {"rack": "rack-4", "resource": "storage_tb", "order": 1, "level": -40, "slope": -1.0,
          "note": "one-off snapshot prune, then ≈25 % less growth"},
         {"rack": "rack-4", "resource": "power_kw", "order": 2, "level": +0.3,
          "note": "≈5 % more CPU on storage nodes for compression"}],
     "risks": ["Write latency for tenant-E rises a little (about 0.1–0.3 ms).",
               "Fewer restore points: snapshots older than 30 days are gone."]},

    {"id": "R5", "kind": "Inefficiency", "priority": "Next quarter",
     "trigger": ("rack-4", "licences"),
     "title": "Reassign 70 idle licence seats to rack-4 (40 from rack-1, 30 from rack-3)",
     "why": "rack-4's seat pool runs out first while rack-1 and rack-3 sit at about 62 % used. "
            "Those idle seats are already paid for, so no new seats are needed.",
     "cost": "$0 if the vendor contract allows reassignment", "lead": "Days",
     "changes": [
         {"rack": "rack-4", "resource": "licences", "order": 1, "capacity": +70, "note": "250 → 320 seats"},
         {"rack": "rack-1", "resource": "licences", "order": 2, "capacity": -40, "note": "250 → 210 seats"},
         {"rack": "rack-3", "resource": "licences", "order": 2, "capacity": -30, "note": "250 → 220 seats"}],
     "risks": ["rack-1 and rack-3 lose licence headroom. Any future growth there now hits the "
               "safe limit sooner.",
               "Better long term: move to a site-wide floating pool (1000 seats, 667 in use). "
               "That needs an HA licence server, because it becomes a shared dependency."]},

    {"id": "R6", "kind": "Inefficiency", "priority": "Next quarter",
     "trigger": ("rack-2", "storage_tb"),
     "title": "Move tenant-C data unread for 90+ days on rack-2 to the object archive tier",
     "why": "rack-2 storage grows by 2.7 TB/week, 85 % of it from tenant-C. Most of that is "
            "write-once data that is rarely read back.",
     "cost": "≈ $1.2k/month archive storage", "lead": "2 weeks",
     "changes": [
         {"rack": "rack-2", "resource": "storage_tb", "order": 1, "level": -60, "slope": -0.8,
          "note": "one-off tiering, then an ongoing lifecycle policy"},
         {"rack": "rack-2", "resource": "network_gbps", "order": 2, "level": +3, "temporary": True,
          "note": "bulk transfer for ≈2 weeks"}],
     "risks": ["Reading archived data takes minutes to hours instead of milliseconds.",
               "Archive egress fees apply if tenant-C reads data back often."]},

    {"id": "R7", "kind": "Inefficiency", "priority": "Next quarter", "trigger": None,
     "title": "Stranded power: put the next 4 GPU nodes on rack-3 instead of a new rack",
     "why": "Every rack draws about 25 kW of its 40 kW budget, so about 37 % of the power "
            "budget is paid for but unused. rack-3 has GPU-ready PDUs and 8U free since the "
            "June move.",
     "cost": "Saves ≈ $60k of new-rack fit-out", "lead": "With the next GPU order",
     "changes": [
         {"rack": "rack-3", "resource": "power_kw", "order": 1, "level": +10,
          "note": "4 × 2.5 kW GPU nodes"},
         {"rack": "rack-3", "resource": "network_gbps", "order": 2, "level": +8,
          "note": "GPU node data ingest"},
         {"rack": "rack-3", "resource": "vm_count", "order": 2, "level": +4, "note": "4 hosts"}],
     "risks": ["This is what caused the June power alarm on rack-3. Cap the build-out at 4 nodes "
               "and enable PDU power capping at 36 kW.",
               "Combined with R1, rack-3 network and power both get tight. Check the combined "
               "plan below before approving both."]},

    {"id": "R8", "kind": "Inefficiency", "priority": "Next quarter", "trigger": None,
     "title": "Find and remove orphaned snapshots and logs on rack-1 and rack-3",
     "why": "Storage on both racks grows by ≈0.6 TB/week even though no tenant's usage is "
            "growing. This creep is typical of stale snapshots, temp files and unrotated logs.",
     "cost": "No capex · 2–3 days of audit", "lead": "2 weeks",
     "changes": [
         {"rack": "rack-1", "resource": "storage_tb", "order": 1, "level": -15, "slope": -0.4,
          "note": "cleanup + lifecycle policy"},
         {"rack": "rack-3", "resource": "storage_tb", "order": 1, "level": -15, "slope": -0.4,
          "note": "cleanup + lifecycle policy"}],
     "risks": ["Some old restore points are lost. Get each tenant to sign off before deleting."]},
]

# Overview plan: (group, one-line bullet) per recommendation, in the order an operator reads it
GROUPS = ["Upgrade", "Optimise", "Check for inefficiency"]
SUMMARY = {
    "R1": ("Optimise", "Move half of tenant-C's traffic from rack-1 to rack-3 (stopgap, days)"),
    "R2": ("Upgrade", "rack-1 ToR uplinks 2 × 50 → 2 × 100 GbE (≈$18k, 3–5 wks)"),
    "R3": ("Upgrade", "rack-4 + 1 JBOD shelf, +250 TB usable (≈$14k, 2–3 wks)"),
    "R4": ("Optimise", "tenant-E: inline compression + 30-day snapshot cap"),
    "R5": ("Optimise", "Move 70 idle licence seats to rack-4 (40 from rack-1, 30 from rack-3)"),
    "R6": ("Optimise", "Archive tenant-C data unread for 90+ days on rack-2"),
    "R7": ("Check for inefficiency", "≈37 % of the power budget is unused: put the next 4 GPU nodes on rack-3"),
    "R8": ("Check for inefficiency", "Storage creeps by 0.6 TB/wk with no tenant growth: find orphaned snapshots and logs"),
}
for _rec in RECOMMENDATIONS:
    _rec["group"], _rec["short"] = SUMMARY[_rec["id"]]


def available(source):
    """Recommendations are hand-written for the synthetic scenario only (for now)."""
    return source == "synthetic"


def _with_temp(changes):
    """Add the derived temperature row for every power row (the 2nd-order chain power → heat)."""
    out = list(changes)
    for c in changes:
        if c["resource"] == "power_kw" and c.get("level"):
            out.append({"rack": c["rack"], "resource": "temp_c", "order": 2,
                        "level": C_PER_KW * c["level"],
                        "note": f"from {c['level']:+.2f} kW at {C_PER_KW} °C/kW"})
    return out


def _state(r):
    """Live (level, slope/week, capacity, margin) of one forecast result."""
    return (r["fc"]["last_level"], 7 * r["fc"]["slope"], r["capacity"],
            r["capacity"] - r["threshold"])


def _weeks_to_safe(level, slope, safe):
    if level >= safe:
        return 0.0
    if slope <= 1e-9:
        return None
    w = (safe - level) / slope
    return round(w, 1) if w <= config.HORIZON_DAYS / 7 else None


def _row(by_key, rack, res, deltas, rec_ids):
    """Before/after for one rack × resource given summed deltas."""
    level, slope, cap, margin = _state(by_key[(rack, res)])
    lv, sl, cp = level + deltas["level"], slope + deltas["slope"], cap + deltas["capacity"]
    after_w = _weeks_to_safe(lv, sl, cp - margin)          # what-if: linear projection
    likely = by_key[(rack, res)]["eta"]["likely"]          # now: the engine's own P50 ETA
    before_w = round(likely / 7, 1) if likely is not None else None
    verdict = ("over" if lv >= cp - margin else
               "tight" if (after_w is not None and after_w < config.WARNING_WEEKS)
               or lv >= cp - margin - 0.05 * cp else "ok")
    return {"rack": rack, "resource": res, "unit": config.UNITS.get(res, ""),
            "before": level, "after": lv, "peak": lv + deltas["temporary"],
            "capacity_before": cap, "capacity_after": cp,
            "safe_before": cap - margin, "safe_after": cp - margin,
            "weeks_before": before_w, "weeks_after": after_w, "verdict": verdict,
            "recs": rec_ids}


def _sum(changes):
    d = {}
    for c in changes:
        k = (c["rack"], c["resource"])
        s = d.setdefault(k, {"level": 0.0, "slope": 0.0, "capacity": 0.0, "temporary": 0.0})
        if c.get("temporary"):
            s["temporary"] += c.get("level", 0)
        else:
            for f in ("level", "slope", "capacity"):
                s[f] += c.get(f, 0)
    return d


def evaluate(results):
    """Each recommendation with its live trigger status and one before/after row per change."""
    by_key = {(r["rack"], r["resource"]): r for r in results if r["fc"]}
    out = []
    for rec in RECOMMENDATIONS:
        changes = [c for c in _with_temp(rec["changes"]) if (c["rack"], c["resource"]) in by_key]
        rows = []
        for c in changes:
            row = _row(by_key, c["rack"], c["resource"], _sum([c])[(c["rack"], c["resource"])],
                       [rec["id"]])
            rows.append({**row, "order": c["order"], "note": c.get("note", ""),
                         "temporary": bool(c.get("temporary"))})
        trig = by_key.get(rec["trigger"]) if rec["trigger"] else None
        out.append({**rec, "rows": rows,
                    "trigger_status": trig["status"] if trig else None})
    return out


def combined(results, ids):
    """Steady state with the chosen recommendations applied together, per rack × resource."""
    by_key = {(r["rack"], r["resource"]): r for r in results if r["fc"]}
    changes, who = [], {}
    for rec in RECOMMENDATIONS:
        if rec["id"] in ids:
            for c in _with_temp(rec["changes"]):
                if (c["rack"], c["resource"]) in by_key:
                    changes.append(c)
                    who.setdefault((c["rack"], c["resource"]), []).append(rec["id"])
    return [_row(by_key, k[0], k[1], d, sorted(set(who[k])))
            for k, d in sorted(_sum(changes).items())]


def touching(rack):
    """Ids of the recommendations that change anything on this rack (incl. side effects)."""
    return [rec["id"] for rec in RECOMMENDATIONS if any(c["rack"] == rack for c in rec["changes"])]

