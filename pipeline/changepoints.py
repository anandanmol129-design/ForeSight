"""Change-point detection on the smoothed daily series.

We run PELT with a piecewise-*linear* cost on the 7-day rolling median, so a steady ramp is
one segment and only steps / slope changes (e.g. a tenant onboarding) create a change point.
Running on the median (not raw data) stops short spikes from becoming change points.
"""
import numpy as np
import pandas as pd

import config

try:
    import ruptures as rpt
except ImportError:          # keep the pipeline alive if ruptures fails to install
    rpt = None


def noise_var(raw):
    """Robust day-to-day noise variance of the raw daily series (MAD of differences)."""
    d = np.diff(raw[~np.isnan(raw)])
    mad = np.median(np.abs(d - np.median(d))) if len(d) else 0.0
    return max((1.4826 * mad) ** 2 / 2, 1e-9)


def _segment_cost(y, t):
    A = np.column_stack([np.ones_like(t), t])
    resid = y - A @ np.linalg.lstsq(A, y, rcond=None)[0]
    return float(resid @ resid)


def _binseg(y, pen, min_size):
    """Fallback: greedy binary segmentation with the same piecewise-linear cost."""
    t = np.arange(len(y), dtype=float)
    bkps, todo = [], [(0, len(y))]
    while todo:
        a, b = todo.pop()
        if b - a < 2 * min_size:
            continue
        whole = _segment_cost(y[a:b], t[a:b])
        gains = [(whole - _segment_cost(y[a:k], t[a:k]) - _segment_cost(y[k:b], t[k:b]), k)
                 for k in range(a + min_size, b - min_size + 1, config.CP_JUMP)]
        gain, k = max(gains)
        if gain > pen:
            bkps.append(k)
            todo += [(a, k), (k, b)]
    return sorted(bkps)


def detect(smooth: pd.Series, raw: pd.Series):
    """Return change-point dates (sorted) for a daily series."""
    s = smooth.dropna()
    n = len(s)
    if n < 2 * config.CP_MIN_SIZE:
        return []
    y = s.values.astype(float)
    pen = config.CP_PENALTY * noise_var(raw.reindex(s.index).values.astype(float)) * np.log(n)
    if rpt is not None:
        signal = np.column_stack([y, np.ones(n), np.arange(n, dtype=float)])
        algo = rpt.Pelt(model="linear", min_size=config.CP_MIN_SIZE, jump=config.CP_JUMP)
        bkps = algo.fit(signal).predict(pen=pen)[:-1]
    else:
        bkps = _binseg(y, pen, config.CP_MIN_SIZE)
    return [s.index[i] for i in bkps]
