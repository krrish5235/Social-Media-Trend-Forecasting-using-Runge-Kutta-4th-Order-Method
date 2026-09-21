"""Score the forecasts against a known ground truth.

Uses the synthetic world in ``app/providers/demo.py``: every video has a true
future, so we can measure (a) median absolute percentage error of the median
forecast and (b) how often the true value falls inside the P10-P90 range
(nominal coverage: 80%).

    python scripts/backtest.py            # 180 synthetic videos
    python scripts/backtest.py -n 600     # tighter estimates

IMPORTANT: this validates the machinery on data that follows the model's own
assumptions (Weibull accumulation). It shows the code is correct and the
intervals are calibrated *in that world*. It does not prove accuracy on real
YouTube data; for that, collect snapshots (scripts/poll_snapshots.py) and
compare early forecasts with what actually happened.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.demo import DemoProvider, World  # noqa: E402
from app.services.report import HORIZONS, build_report  # noqa: E402

AGES = (0.4, 1.0, 2.0, 4.0, 7.0, 14.0)


def evaluate(n: int, n_draws: int = 300, prefix: str = "bt"):
    now = datetime.now(timezone.utc)
    rows = {"cohort": [], "with_history": []}
    for i in range(n):
        vid = f"{prefix}{i:08d}"
        age = AGES[i % len(AGES)]
        bundle = DemoProvider().fetch(vid, now=now, target_age_days=age)
        world = World(vid, age)
        truth = {h: world.true_views(age + h) for h in HORIZONS}
        for name, b in (("cohort", replace(bundle, history=None)), ("with_history", bundle)):
            rep = build_report(b, n_draws=n_draws)
            for h in rep["forecast"]["horizons"]:
                t = truth[h["days"]]
                rows[name].append(
                    dict(age=age, h=h["days"], ape=abs(h["p50"] - t) / t, hit=h["p10"] <= t <= h["p90"], width=np.log(max(h["p90"], 1) / max(h["p10"], 1)))
                )
    return rows


def summarise(rows):
    out = {}
    for name, items in rows.items():
        by_h = defaultdict(list)
        by_a = defaultdict(list)
        for r in items:
            by_h[r["h"]].append(r)
            by_a[r["age"]].append(r)
        out[name] = {
            "by_horizon": {h: (float(np.median([r["ape"] for r in v])), float(np.mean([r["hit"] for r in v]))) for h, v in sorted(by_h.items())},
            "by_age": {a: float(np.mean([r["hit"] for r in v])) for a, v in sorted(by_a.items())},
            "overall_coverage": float(np.mean([r["hit"] for r in items])),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=180, help="number of synthetic videos")
    args = ap.parse_args()
    res = summarise(evaluate(args.n))
    for name, r in res.items():
        print(f"\n== {name} ==  overall P10-P90 coverage: {r['overall_coverage']:.0%} (nominal 80%)")
        print("horizon   median error   coverage")
        for h, (ape, cov) in r["by_horizon"].items():
            print(f"{h:>5} d   {ape:>9.1%}   {cov:>9.0%}")
        print("coverage by video age:", {f"{a:g}d": f"{c:.0%}" for a, c in r["by_age"].items()})


if __name__ == "__main__":
    main()
