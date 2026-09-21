"""Cohort model: how do Shorts *from this channel* accumulate views with age?

Idea
----
A Short's cumulative views follow ``V(age) = M * F(age)`` where ``M`` is the
video's eventual (lifetime) views and ``F`` is a normalised accumulation curve
rising from 0 to 1. We model ``F`` as a Weibull CDF

    F(a) = 1 - exp(-(a / tau) ** beta)

(``beta < 1`` gives the fast-start / long-tail shape typical of Shorts).

The public YouTube API only exposes *today's* counters, not history. But a
channel's recent Shorts all have different ages, so together they trace out
the channel's typical curve. We fit ``log V_i = log M + log F(age_i)`` across
those peers with a robust loss and a weak prior on the curve shape.

Uncertainty
-----------
Cross-sectional peers identify the *early* curve well but the *tail* poorly
(the tail is a few percent of views, hidden in large video-to-video noise).
A point estimate would hide that. We therefore approximate the posterior of
``(log tau, beta)`` with a Laplace approximation (Gaussian around the MAP fit
with covariance ``(J'J)^-1``, where ``J`` includes the prior rows), and draw
forecast curves from it. When the data say little, the draws stay wide.

On top of that, an individual video deviates from its channel's curve; that
is modelled with log-normal jitter on ``tau`` and ``beta`` per draw.

For a target video with age ``a`` and views ``V`` the forecast at a later
age ``a'`` is ``V * F(a') / F(a)`` -- only the *shape* matters, which is why
the model works for any channel size.

Known limitation: peers are assumed to share one level distribution. If a
channel grew a lot recently, old and new Shorts differ in ``M`` and the shape
is biased. See README "Limitations".
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import least_squares

# Weak prior on the curve shape (typical Shorts: ~55% of lifetime views in day 1).
PRIOR_TAU = 1.5  # days
PRIOR_BETA = 0.6
SIGMA_LOG_TAU = 1.0
SIGMA_BETA = 0.3
SIGMA_DATA = 0.8  # starting guess for video-to-video spread of log-views
DEFAULT_TAU_DISPERSION = 0.25
DEFAULT_BETA_DISPERSION = 0.08

MIN_AGE_DAYS = 1.0 / 24.0
TAU_BOUNDS = (0.05, 60.0)
BETA_BOUNDS = (0.25, 1.5)

_LO = np.array([0.0, math.log(TAU_BOUNDS[0]), BETA_BOUNDS[0]])
_HI = np.array([28.0, math.log(TAU_BOUNDS[1]), BETA_BOUNDS[1]])


def weibull_cdf(age_days, tau, beta):
    """Normalised accumulation curve F(age); F(0)=0 and F(inf)=1."""
    a = np.maximum(np.asarray(age_days, dtype=float), 0.0)
    return -np.expm1(-((a / tau) ** beta))


@dataclass(frozen=True)
class CohortFit:
    log_m: float
    tau: float
    beta: float
    n: int
    resid_std: float  # robust spread of log-views around the fitted curve
    cov: np.ndarray  # 3x3 posterior covariance of (log_m, log_tau, beta)

    @property
    def m(self) -> float:
        return math.exp(self.log_m)


def _residuals(theta, ages, logv, sigma_data):
    log_m, log_tau, beta = theta
    f = weibull_cdf(ages, math.exp(log_tau), beta)
    pred = log_m + np.log(np.maximum(f, 1e-300))
    data_res = (logv - pred) / sigma_data
    prior = [(log_tau - math.log(PRIOR_TAU)) / SIGMA_LOG_TAU, (beta - PRIOR_BETA) / SIGMA_BETA]
    return np.concatenate([data_res, prior])


def _solve(ages, logv, x0, sigma_data):
    return least_squares(
        _residuals, x0, args=(ages, logv, sigma_data), bounds=(_LO, _HI), loss="soft_l1", f_scale=2.0
    )


def fit_cohort(ages, views, x0: Optional[np.ndarray] = None) -> CohortFit:
    """MAP fit of (log M, tau, beta) to peer (age, views) pairs, with posterior covariance."""
    ages = np.maximum(np.asarray(ages, dtype=float), MIN_AGE_DAYS)
    logv = np.log1p(np.maximum(np.asarray(views, dtype=float), 0.0))
    n = len(ages)
    if x0 is None:
        if n:
            f_prior = weibull_cdf(ages, PRIOR_TAU, PRIOR_BETA)
            log_m0 = float(np.median(logv - np.log(np.maximum(f_prior, 1e-12))))
        else:
            log_m0 = math.log(1e4)
        x0 = np.array([log_m0, math.log(PRIOR_TAU), PRIOR_BETA])
    x0 = np.clip(x0, _LO + 1e-9, _HI - 1e-9)

    sol = _solve(ages, logv, x0, SIGMA_DATA)
    # Second pass: re-scale the data noise to what this channel actually shows.
    data_resid = logv - (sol.x[0] + np.log(np.maximum(weibull_cdf(ages, math.exp(sol.x[1]), sol.x[2]), 1e-300)))
    if n >= 5:
        mad = 1.4826 * float(np.median(np.abs(data_resid - np.median(data_resid))))
        sigma = float(np.clip(mad, 0.3, 3.0))
        sol = _solve(ages, logv, sol.x, sigma)
    else:
        sigma = SIGMA_DATA
    jtj = sol.jac.T @ sol.jac
    cov = np.linalg.pinv(jtj)
    return CohortFit(
        log_m=float(sol.x[0]),
        tau=float(math.exp(sol.x[1])),
        beta=float(sol.x[2]),
        n=n,
        resid_std=float(sigma),
        cov=cov,
    )


class CohortModel:
    """Fitted accumulation curve plus a posterior ensemble of plausible curves."""

    MIN_PEERS_FOR_FIT = 3

    def __init__(
        self,
        peer_ages,
        peer_views,
        *,
        n_draws: int = 400,
        tau_dispersion: float = DEFAULT_TAU_DISPERSION,
        beta_dispersion: float = DEFAULT_BETA_DISPERSION,
        seed: int = 0,
    ) -> None:
        """
        Parameters
        ----------
        tau_dispersion, beta_dispersion:
            How much a *single* video's accumulation curve deviates from the
            channel curve (log-normal sigma on ``tau`` and ``beta``). Peers give
            one snapshot each, so this cannot be estimated from them; it is a
            modelling constant. Larger values widen forecast intervals, most
            strongly for older videos whose remaining growth is all "tail".
        """
        self.peer_ages = np.maximum(np.asarray(peer_ages, dtype=float), MIN_AGE_DAYS)
        self.peer_views = np.maximum(np.asarray(peer_views, dtype=float), 0.0)
        rng = np.random.default_rng(seed)
        n = len(self.peer_ages)

        self.prior_only = n < self.MIN_PEERS_FOR_FIT
        self.fit: Optional[CohortFit] = None

        if self.prior_only:
            log_tau = rng.normal(math.log(PRIOR_TAU), SIGMA_LOG_TAU, n_draws)
            beta = rng.normal(PRIOR_BETA, SIGMA_BETA, n_draws)
        else:
            self.fit = fit_cohort(self.peer_ages, self.peer_views)
            mean = np.array([math.log(self.fit.tau), self.fit.beta])
            cov = self.fit.cov[1:, 1:]
            cov = (cov + cov.T) / 2.0 + 1e-12 * np.eye(2)
            draws = rng.multivariate_normal(mean, cov, size=n_draws)
            log_tau, beta = draws[:, 0], draws[:, 1]

        self.channel_shapes = np.column_stack(
            [
                np.exp(np.clip(log_tau, _LO[1], _HI[1])),
                np.clip(beta, BETA_BOUNDS[0], BETA_BOUNDS[1]),
            ]
        )
        # Add this video's own deviation from the channel curve to every draw.
        tau_i = self.channel_shapes[:, 0] * np.exp(tau_dispersion * rng.standard_normal(n_draws))
        beta_i = self.channel_shapes[:, 1] * np.exp(beta_dispersion * rng.standard_normal(n_draws))
        self.video_shapes = np.column_stack(
            [np.clip(tau_i, *TAU_BOUNDS), np.clip(beta_i, *BETA_BOUNDS)]
        )

    # -- point estimates --------------------------------------------------
    def curve(self, age) -> np.ndarray:
        """Accumulation curve F at the fitted parameters (prior mean if no fit)."""
        tau = self.fit.tau if self.fit else PRIOR_TAU
        beta = self.fit.beta if self.fit else PRIOR_BETA
        return weibull_cdf(np.maximum(age, 0.0), tau, beta)

    def expected_views(self, age: float) -> Optional[float]:
        """Views of a *typical* Short of this channel at ``age`` (None without a fit)."""
        if not self.fit:
            return None
        return float(self.fit.m * self.curve(max(age, MIN_AGE_DAYS)))

    def peer_performance(self) -> np.ndarray:
        """Each peer's views relative to the typical Short at the same age."""
        if not self.fit:
            return np.ones(len(self.peer_ages))
        exp_views = self.fit.m * self.curve(self.peer_ages)
        return (self.peer_views + 1.0) / np.maximum(exp_views, 1e-9)

    # -- forecasting ------------------------------------------------------
    def project(self, age: float, views: float, times) -> np.ndarray:
        """Ensemble of cumulative-view curves, shape (n_draws, len(times))."""
        age = max(float(age), MIN_AGE_DAYS)
        times = np.asarray(times, dtype=float)
        tau = self.video_shapes[:, 0:1]
        beta = self.video_shapes[:, 1:2]
        f_now = weibull_cdf(age, tau, beta)  # (B, 1)
        f_t = weibull_cdf(times[None, :], tau, beta)  # (B, T)
        return float(views) * f_t / np.maximum(f_now, 1e-300)

    def bands(self, age: float, views: float, times) -> dict:
        curves = self.project(age, views, times)
        p10, p50, p90 = np.percentile(curves, [10, 50, 90], axis=0)
        return {"p10": p10, "p50": p50, "p90": p90}
