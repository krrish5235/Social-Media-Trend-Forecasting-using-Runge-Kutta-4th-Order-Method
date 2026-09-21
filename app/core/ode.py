"""Runge-Kutta 4th order integrator.

This is the numerical heart of the original project. It is used by the
live-tracking model (``live.py``) to integrate the logistic growth ODE

    dV/dt = r * V * (1 - V / K)

where V is cumulative views, r the growth rate and K the saturation level.
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

import numpy as np

Rhs = Callable[[float, float], float]


def rk4_step(f: Rhs, t: float, y: float, dt: float) -> float:
    """Advance ``y' = f(t, y)`` by one classical RK4 step of size ``dt``."""
    k1 = f(t, y)
    k2 = f(t + dt / 2.0, y + dt * k1 / 2.0)
    k3 = f(t + dt / 2.0, y + dt * k2 / 2.0)
    k4 = f(t + dt, y + dt * k3)
    return y + dt * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0


def rk4_solve_at(
    f: Rhs,
    y0: float,
    t0: float,
    times: Sequence[float],
    dt_max: float = 1.0 / 24.0,
) -> np.ndarray:
    """Integrate from ``(t0, y0)`` and return ``y`` at every entry of ``times``.

    ``times`` must be ascending and all ``>= t0``. Between consecutive output
    times the interval is split into equal sub-steps no larger than ``dt_max``,
    so results are accurate at exactly the requested times.
    """
    out = []
    t, y = float(t0), float(y0)
    for target in times:
        target = float(target)
        if target < t - 1e-12:
            raise ValueError("times must be ascending and >= t0")
        span = target - t
        if span > 0.0:
            n = max(1, math.ceil(span / dt_max))
            h = span / n
            for _ in range(n):
                y = rk4_step(f, t, y, h)
                t += h
            t = target
        out.append(y)
    return np.asarray(out, dtype=float)


def logistic_rhs(r: float, k: float) -> Rhs:
    """Right-hand side of the logistic growth ODE."""

    def rhs(_t: float, v: float) -> float:
        return r * v * (1.0 - v / k)

    return rhs


def logistic_exact(t, v0: float, r: float, k: float, t0: float = 0.0):
    """Closed-form logistic solution, used to validate the RK4 integrator."""
    t = np.asarray(t, dtype=float)
    return k / (1.0 + ((k - v0) / v0) * np.exp(-r * (t - t0)))
