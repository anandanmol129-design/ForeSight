"""When does each forecast band cross capacity - safety_margin?"""
import numpy as np


def first_crossing(band, threshold):
    """Days until `band` first reaches threshold (1-based), or None within the horizon."""
    idx = np.nonzero(band >= threshold)[0]
    return int(idx[0]) + 1 if len(idx) else None


def eta(fc, threshold):
    """earliest = P90 crossing, likely = P50, latest = P10 (days from the cutoff)."""
    if fc is None:
        return {"earliest": None, "likely": None, "latest": None}
    if fc["last_level"] >= threshold:                 # already past the safe limit
        return {"earliest": 0, "likely": 0, "latest": 0}
    return {"earliest": first_crossing(fc["p90"], threshold),
            "likely": first_crossing(fc["p50"], threshold),
            "latest": first_crossing(fc["p10"], threshold)}


def weeks(days):
    return None if days is None or days != days else round(days / 7, 1)   # None / NaN safe


def describe(rack, resource, e, horizon_days):
    """e.g. 'rack-2 storage_tb: likely in 4.1 weeks (range 3.0-6.2)'."""
    if e["likely"] is None:
        if e["earliest"] is not None:
            return (f"{rack} {resource}: not expected within {horizon_days // 7} weeks "
                    f"(worst case {weeks(e['earliest'])} weeks)")
        return f"{rack} {resource}: no exhaustion expected within {horizon_days // 7} weeks"
    if e["likely"] == 0:
        return f"{rack} {resource}: ALREADY past the safety margin"
    latest = f"{weeks(e['latest'])}" if e["latest"] is not None else f">{horizon_days // 7}"
    return (f"{rack} {resource}: likely in {weeks(e['likely'])} weeks "
            f"(range {weeks(e['earliest'])}–{latest})")
