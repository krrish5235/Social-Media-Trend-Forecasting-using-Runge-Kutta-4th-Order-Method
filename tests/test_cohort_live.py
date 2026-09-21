import numpy as np

from app.core.cohort import CohortModel, fit_cohort, weibull_cdf
from app.core.live import fit_live_logistic
from app.core.ode import logistic_exact


def _peers(tau=1.3, beta=0.6, n=40, sigma=0.5, seed=0):
    rng = np.random.default_rng(seed)
    ages = np.concatenate([rng.uniform(0.2, 5, n // 2), rng.uniform(5, 60, n - n // 2)])
    m = 50_000 * np.exp(rng.normal(0, sigma, n))
    return ages, m * weibull_cdf(ages, tau, beta)


def test_weibull_is_a_valid_accumulation_curve():
    a = np.array([0, 0.1, 1, 10, 1000, np.inf])
    f = weibull_cdf(a, 1.5, 0.6)
    assert f[0] == 0 and f[-1] == 1
    assert np.all(np.diff(f) >= 0) and np.all((f >= 0) & (f <= 1))


def test_fit_recovers_curve_shape_from_peers():
    ages, views = _peers()
    fit = fit_cohort(ages, views)
    # judged by what matters for forecasting: the fraction of lifetime views by day 1 and 7
    for day in (1, 7):
        assert abs(weibull_cdf(day, fit.tau, fit.beta) - weibull_cdf(day, 1.3, 0.6)) < 0.08


def test_bands_are_ordered_and_monotone():
    ages, views = _peers()
    m = CohortModel(ages, views, seed=3)
    times = np.array([2, 3, 5, 9, 20, 60], dtype=float)
    b = m.bands(2.0, 10_000.0, times)
    assert np.all(b["p10"] <= b["p50"]) and np.all(b["p50"] <= b["p90"])
    assert np.all(np.diff(b["p50"]) >= 0)
    assert np.all(b["p10"] >= 10_000.0 - 1e-6)  # never below today's views


def test_few_peers_fall_back_to_a_wide_prior():
    good = CohortModel(*_peers(), seed=1)
    prior = CohortModel([1.0], [500.0], seed=1)
    assert prior.prior_only and prior.fit is None and not good.prior_only
    t = np.array([9.0])
    width = lambda m: np.log(m.bands(2.0, 1000.0, t)["p90"] / m.bands(2.0, 1000.0, t)["p10"])[0]
    assert width(prior) > width(good)


def test_model_is_deterministic_for_a_seed():
    ages, views = _peers()
    a = CohortModel(ages, views, seed=9).bands(3.0, 5000.0, [10.0])
    b = CohortModel(ages, views, seed=9).bands(3.0, 5000.0, [10.0])
    assert np.allclose(a["p50"], b["p50"]) and np.allclose(a["p90"], b["p90"])


def test_live_fit_recovers_logistic_parameters():
    t = np.array([0.1, 0.3, 0.6, 1.0, 1.5, 2.0, 3.0, 4.0])
    v = logistic_exact(t, 200.0, 2.0, 50_000.0, t0=0.1)
    fit = fit_live_logistic(t, v)
    assert fit is not None and fit.rmse_log < 0.02
    assert abs(fit.r - 2.0) / 2.0 < 0.15
    ahead = fit.forecast(4.0, float(v[-1]), [5.0, 10.0])
    assert np.all(np.diff(ahead) > 0) and ahead[-1] <= fit.k * 1.0001


def test_live_fit_refuses_thin_or_flat_data():
    assert fit_live_logistic([0.1, 0.2, 0.3], [10, 20, 30]) is None  # too few points
    assert fit_live_logistic([1, 2, 3, 4], [100, 100, 100, 100]) is None  # no growth
    assert fit_live_logistic([1, 1.01, 1.02, 1.03], [100, 110, 120, 130]) is None  # span too short
