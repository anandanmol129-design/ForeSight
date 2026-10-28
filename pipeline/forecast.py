"""Linear-trend forecast with P10/P50/P90 bands, fitted after the most recent change point."""
import hashlib

import numpy as np
import pandas as pd

import config
from pipeline import changepoints

CP_SEARCH_DAYS = 240   # only look this far back for change points (keeps the backtest fast)


def smooth(raw: pd.Series) -> pd.Series:
    """Trailing 7-day rolling median: no look-ahead, and 1-3 day spikes vanish."""
    return raw.rolling(config.ROLL_DAYS, min_periods=config.ROLL_DAYS // 2 + 1).median()


def fit_window(cps, cutoff):
    """(fit start, level-shift date or None).

    Normally: fit from the last change point. If it is < MIN_FIT_DAYS old, fall back to the
    last 60 days, but fit a level shift at that change point so a fresh step (e.g. a tenant
    onboarding) moves the level without being mistaken for a steep slope.
    """
    if cps and (cutoff - cps[-1]).days >= config.MIN_FIT_DAYS:
        return cps[-1], None
    if cps:
        return cutoff - pd.Timedelta(days=config.FALLBACK_FIT_DAYS - 1), cps[-1]
    return cutoff - pd.Timedelta(days=config.MAX_FIT_DAYS - 1), None


def search_start(raw, cutoff):
    """Change-point search window start, snapped to a fixed grid so PELT's candidate
    positions (multiples of CP_JUMP) do not move from one daily run to the next."""
    origin = raw.index[0]
    days = max((cutoff - origin).days - CP_SEARCH_DAYS, 0)
    return origin + pd.Timedelta(days=days - days % config.CP_JUMP)


def slice_hash(s: pd.Series) -> str:
    payload = s.index.astype("int64").values.tobytes() + s.values.astype("float64").tobytes()
    return hashlib.sha256(payload).hexdigest()


def forecast(raw: pd.Series, cutoff=None, fit_start=None, cps=None):
    """Fit on data up to and including `cutoff`; returns a dict describing the forecast.

    `fit_start` / `cps` can be passed in to reuse a window (used for tenant attribution).
    """
    cutoff = raw.index[-1] if cutoff is None else pd.Timestamp(cutoff)
    raw = raw.loc[:cutoff]
    sm = smooth(raw)
    if cps is None:
        start = search_start(raw, cutoff)
        cps = changepoints.detect(sm.loc[start:], raw.loc[start:])
    shift_at = None
    if fit_start is None:
        fit_start, shift_at = fit_window(cps, cutoff)
    win = sm.loc[fit_start:cutoff].dropna()
    if len(win) < 7:
        return None

    def design(idx):   # columns: days from cutoff (0 = cutoff day), 1, [after level shift]
        cols = [(idx - cutoff).days.values.astype(float), np.ones(len(idx))]
        return np.column_stack(cols + ([(idx >= shift_at).astype(float)] if shift_at else []))

    coef = np.linalg.lstsq(design(win.index), win.values, rcond=None)[0]
    ss_res = float(np.sum((win.values - design(win.index) @ coef) ** 2))
    ss_tot = float(np.sum((win.values - win.values.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0     # goodness of the trend fit
    slope, intercept = coef[0], coef[1] + (coef[2] if shift_at else 0.0)
    # Band width: robust (MAD) std of the RAW daily values around the trend, so weekly
    # seasonality and day-to-day noise count, but a one-off spike does not blow up the band.
    rw = raw.loc[win.index[0]:cutoff].dropna()
    resid = rw.values - design(rw.index) @ coef
    resid_std = float(1.4826 * np.median(np.abs(resid - np.median(resid))))

    h = np.arange(1, config.HORIZON_DAYS + 1, dtype=float)
    p50 = intercept + slope * h
    spread = config.Z_80 * resid_std * np.sqrt(h)
    return {
        "cutoff": cutoff, "fit_start": win.index[0], "fit_end": win.index[-1],
        "change_points": cps, "slope": float(slope), "intercept": float(intercept),
        "level_shift_at": shift_at, "resid_std": resid_std, "n_fit": len(win), "r2": r2,
        "last_level": float(win.iloc[-1]),
        "dates": cutoff + pd.to_timedelta(h, unit="D"),
        "p10": p50 - spread, "p50": p50, "p90": p50 + spread,
        "input_sha256": slice_hash(raw.loc[win.index[0]:cutoff]),
    }
