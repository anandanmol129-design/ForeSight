"""Append-only JSONL audit trail: every forecast, the exact data slice it used, and its output."""
import json
import uuid
from datetime import datetime, timezone

import config


def log_path():
    """Resolved per call: the app can switch data-centre records while running."""
    return config.AUDIT_DIR / "forecast_log.jsonl"


def _d(x):
    return None if x is None else str(x)[:10]


def new_run_id():
    return uuid.uuid4().hex[:12]


def record(run_id, viewer, rack, resource, fc, eta=None, status=None, driver=None,
           run_type="live", tenant=None, sustain_history=None, confidence=None):
    return {
        "run_id": run_id, "run_type": run_type,
        "run_timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "viewer_role": viewer, "rack": rack, "resource": resource, "tenant": tenant,
        "data_cutoff": _d(fc and fc["cutoff"]),
        "fit_window_start": _d(fc and fc["fit_start"]), "fit_window_end": _d(fc and fc["fit_end"]),
        "change_points_used": [_d(c) for c in (fc["change_points"] if fc else [])],
        "model_params": None if fc is None else {
            "slope_per_day": round(fc["slope"], 6), "intercept": round(fc["intercept"], 4),
            "resid_std": round(fc["resid_std"], 6), "n_fit_days": fc["n_fit"],
            "level_shift_at": _d(fc["level_shift_at"]), "r2": round(fc["r2"], 4)},
        "input_sha256": fc and fc["input_sha256"],
        "outputs": {"eta_days": eta, "severity": status, "top_driver": driver,
                    "p50_eta_last_runs": sustain_history, "confidence": confidence},
    }


def append(records, path=None):
    """Append only - existing lines are never rewritten."""
    path = path or log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")


def tail(n=200, path=None):
    path = path or log_path()
    if not path.exists():
        return []
    with open(path) as f:
        lines = f.readlines()[-n:]
    return [json.loads(l) for l in reversed(lines)]
