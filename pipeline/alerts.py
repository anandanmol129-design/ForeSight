"""Turn a sequence of daily ETA results into an alert (sustained) or HOLD (not yet sustained)."""
import config


def severity(likely_days):
    if likely_days is None:
        return "OK"
    if likely_days < config.CRITICAL_WEEKS * 7:
        return "CRITICAL"
    if likely_days < config.WARNING_WEEKS * 7:
        return "WARNING"
    return "PLAN"


def classify(recent_likely, fresh_regime=False):
    """`recent_likely`: P50 crossing days for the last SUSTAIN_RUNS runs, oldest first.

    An alert is raised only if every one of those runs saw a P50 crossing; a crossing on
    the latest run alone is a HOLD (likely a spike or a fresh, unconfirmed trend).
    `fresh_regime`: the newest change point is < MIN_FIT_DAYS old, so the slope is not yet
    trustworthy - only CRITICAL may fire; PLAN/WARNING wait as HOLD.
    """
    latest = recent_likely[-1]
    if latest is None:
        return "OK"
    sustained = (len(recent_likely) >= config.SUSTAIN_RUNS
                 and all(d is not None for d in recent_likely[-config.SUSTAIN_RUNS:]))
    sev = severity(latest)
    if not sustained or (fresh_regime and sev != "CRITICAL"):
        return "HOLD"
    return sev


def recommendation(status):
    return config.RECOMMENDATION[status]
