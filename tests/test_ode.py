import numpy as np

from app.core.ode import logistic_exact, logistic_rhs, rk4_solve_at, rk4_step


def test_rk4_matches_closed_form_logistic():
    r, k, v0 = 0.8, 1e5, 500.0
    times = np.linspace(0.5, 10, 20)
    numeric = rk4_solve_at(logistic_rhs(r, k), v0, 0.0, times, dt_max=0.05)
    exact = logistic_exact(times, v0, r, k)
    assert np.max(np.abs(numeric - exact) / exact) < 1e-6


def test_rk4_is_fourth_order():
    """Halving the step should shrink the error ~16x (2^4)."""
    r, k, v0, t_end = 1.0, 1000.0, 10.0, 5.0
    exact = float(logistic_exact(t_end, v0, r, k))
    errors = []
    for n in (10, 20, 40):
        dt = t_end / n
        y, t = v0, 0.0
        for _ in range(n):
            y = rk4_step(logistic_rhs(r, k), t, y, dt)
            t += dt
        errors.append(abs(y - exact))
    assert 12 < errors[0] / errors[1] < 20
    assert 12 < errors[1] / errors[2] < 20


def test_logistic_stays_below_carrying_capacity():
    out = rk4_solve_at(logistic_rhs(2.0, 1000.0), 1.0, 0.0, [1, 5, 20, 100])
    assert np.all(out < 1000.0) and np.all(np.diff(out) >= 0)


def test_rejects_unsorted_times():
    import pytest

    with pytest.raises(ValueError):
        rk4_solve_at(logistic_rhs(1, 10), 1.0, 5.0, [4.0])
