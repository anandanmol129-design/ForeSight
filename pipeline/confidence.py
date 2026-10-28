"""How much should an operator trust an alert? A 0-100 score from four plain checks.

  band       (35%)  how tight the ETA range is relative to the likely ETA
  fit        (25%)  R^2 of the trend line on the smoothed fit window
  data       (20%)  days of data behind the fit (6 weeks or more = full marks)
  stability  (20%)  do the last 3 daily runs agree on the ETA?
"""
import numpy as np

import config

WEIGHTS = {"band": 0.35, "fit": 0.25, "data": 0.20, "stability": 0.20}
FULL_DATA_DAYS = 42


def score(fc, eta, history):
    """Returns {"score": 0-100, "label": High/Medium/Low, "parts": {...}} or None (no ETA)."""
    likely = eta["likely"]
    if fc is None or likely is None:
        return None
    if likely == 0:                                       # already past the limit: observed
        parts = {"band": 1.0, "fit": 1.0, "data": 1.0, "stability": 1.0}
    else:
        latest = eta["latest"] if eta["latest"] is not None else config.HORIZON_DAYS * 1.5
        earliest = eta["earliest"] or 0
        runs = [d for d in history if d is not None]
        parts = {
            "band": 1 - min(1.0, (latest - earliest) / (2 * likely)),
            "fit": float(np.clip(fc["r2"], 0, 1)),
            "data": min(1.0, fc["n_fit"] / FULL_DATA_DAYS),
            "stability": (1 - min(1.0, np.std(runs) / likely)) * len(runs) / len(history),
        }
    total = round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()))
    label = "High" if total >= 75 else "Medium" if total >= 50 else "Low"
    return {"score": total, "label": label,
            "parts": {k: round(float(v), 2) for k, v in parts.items()}}
