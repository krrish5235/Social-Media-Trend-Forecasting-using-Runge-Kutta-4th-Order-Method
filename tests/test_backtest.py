"""Calibration guard: if a change breaks the model or its intervals, this fails."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("backtest", Path(__file__).resolve().parents[1] / "scripts" / "backtest.py")
backtest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backtest)


def test_synthetic_backtest_is_accurate_and_calibrated():
    res = backtest.summarise(backtest.evaluate(48, n_draws=150, prefix="ci"))
    for name in ("cohort", "with_history"):
        r = res[name]
        assert 0.75 <= r["overall_coverage"] <= 0.99, (name, r["overall_coverage"])  # nominal 80%, may be conservative
        assert r["by_horizon"][7][0] < 0.12  # median error at 7 days
        assert r["by_horizon"][1][0] < 0.08
