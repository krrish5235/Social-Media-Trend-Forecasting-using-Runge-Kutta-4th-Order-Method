"""Live-tracking model: logistic growth fitted to a video's *own* snapshots.

Once a video has been observed several times (from the snapshot store, or from
YouTube Analytics data for your own channel) we can fit

    dV/dt = r * V * (1 - V / K)

directly to its trajectory. The ODE is integrated with RK4 (see ``ode.py``) and
``(r, K)`` are found by least squares on log-views.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from scipy.optimize import least_squares

from .ode import logistic_rhs, rk4_solve_at

MIN_POINTS = 4  # 2 parameters -> need at least 3 residual degrees of freedom
MIN_SPAN_DAYS = 0.25
MAX_POINTS = 120
MAX_RMSE_LOG = 0.25  # reject fits that do not describe the trajectory
R_BOUNDS = (0.01, 30.0)  # per day; keeps RK4 stable at dt = 1 hour
_STARTS = ((0.5, 0.0), (2.0, 1.0), (6.0, 2.0), (1.0, -2.0))  # (r0, log-headroom0)


@dataclass(frozen=True)
class LiveFit:
    r: float
    k: float
    rmse_log: float
    n: int
    span_days: float

    def forecast(self, age: float, views: float, times: Sequence[float]) -> np.ndarray:
        """Cumulative views at ``times`` (ascending, >= age), starting from now."""
        return rk4_solve_at(logistic_rhs(self.r, max(self.k, views * 1.0001)), views, age, times)


def _prepare(t_days, views):
    t = np.asarray(t_days, dtype=float)
    v = np.asarray(views, dtype=float)
    order = np.argsort(t, kind="stable")
    t, v = t[order], np.maximum.accumulate(v[order])  # cumulative counters never decrease
    keep = np.concatenate([[True], np.diff(t) > 1e-9])  # drop duplicate timestamps
    t, v = t[keep], v[keep]
    if len(t) > MAX_POINTS:
        idx = np.unique(np.linspace(0, len(t) - 1, MAX_POINTS).astype(int))
        t, v = t[idx], v[idx]
    return t, v


def fit_live_logistic(t_days, views) -> Optional[LiveFit]:
    """Fit (r, K) to a snapshot series. Returns None if the data cannot support it."""
    t, v = _prepare(t_days, views)
    if len(t) < MIN_POINTS or t[-1] - t[0] < MIN_SPAN_DAYS or v[0] <= 0 or v[-1] <= v[0] * 1.001:
        return None
    t0, v0, v_last = float(t[0]), float(v[0]), float(v[-1])
    log_target = np.log(v[1:])

    def simulate(theta):
        r = math.exp(theta[0])
        k = v_last * (1.0 + math.exp(theta[1]))
        return rk4_solve_at(logistic_rhs(r, k), v0, t0, t[1:])

    def residuals(theta):
        return np.log(np.maximum(simulate(theta), 1.0)) - log_target

    lo = np.array([math.log(R_BOUNDS[0]), -8.0])
    hi = np.array([math.log(R_BOUNDS[1]), 8.0])
    best = None
    for r0, headroom0 in _STARTS:
        x0 = np.clip(np.array([math.log(r0), headroom0]), lo + 1e-9, hi - 1e-9)
        try:
            sol = least_squares(residuals, x0, bounds=(lo, hi))
        except Exception:  # pragma: no cover - defensive
            continue
        if best is None or sol.cost < best.cost:
            best = sol
    if best is None:
        return None
    rmse = float(math.sqrt(np.mean(best.fun**2)))
    if not math.isfinite(rmse) or rmse > MAX_RMSE_LOG:
        return None
    return LiveFit(
        r=float(math.exp(best.x[0])),
        k=float(v_last * (1.0 + math.exp(best.x[1]))),
        rmse_log=rmse,
        n=int(len(t)),
        span_days=float(t[-1] - t[0]),
    )
