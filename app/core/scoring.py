"""Interpretation helpers: percentile ranks and the Momentum Score.

The Momentum Score is a *descriptive* heuristic that summarises how well a
Short is doing relative to its own channel. It does not feed the numeric
forecast (the forecast uses only the growth-curve models). Weights are
hand-set and documented in the README.
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

WEIGHTS = {"velocity": 0.50, "engagement": 0.25, "reach": 0.15, "sentiment": 0.10}
TIERS = ((75.0, "Breakout"), (55.0, "Strong"), (35.0, "Steady"), (0.0, "Slow"))


def percentile_rank(value: float, sample: Sequence[float]) -> Optional[float]:
    """Share of ``sample`` below ``value`` (ties count half). None if empty."""
    arr = np.asarray(sample, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    return float((np.sum(arr < value) + 0.5 * np.sum(arr == value)) / arr.size)


def reach_score(reach_ratio: float) -> float:
    """Map views/subscribers onto 0..1 (10x the subscriber count saturates)."""
    return min(1.0, math.log10(1.0 + max(reach_ratio, 0.0)) / math.log10(11.0))


def momentum(
    velocity_pct: Optional[float],
    engagement_pct: Optional[float],
    reach_ratio: Optional[float],
    sentiment_mean: Optional[float],
) -> Tuple[float, str, Dict[str, float]]:
    """Return (score 0-100, tier, component scores 0-100). Missing parts are re-weighted away."""
    parts: Dict[str, float] = {}
    if velocity_pct is not None:
        parts["velocity"] = velocity_pct
    if engagement_pct is not None:
        parts["engagement"] = engagement_pct
    if reach_ratio is not None:
        parts["reach"] = reach_score(reach_ratio)
    if sentiment_mean is not None:
        parts["sentiment"] = (sentiment_mean + 1.0) / 2.0
    if not parts:
        return 50.0, "Steady", {}
    total_w = sum(WEIGHTS[k] for k in parts)
    value = 100.0 * sum(parts[k] * WEIGHTS[k] for k in parts) / total_w
    tier = next(name for threshold, name in TIERS if value >= threshold)
    return round(value, 1), tier, {k: round(100.0 * v, 1) for k, v in parts.items()}
