import math
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest

from app.providers.demo import DemoProvider
from app.services.report import AnalysisError, HORIZONS, build_report, next_round_numbers


def test_report_shape_and_invariants(demo_bundle):
    r = build_report(demo_bundle, n_draws=200)
    f = r["forecast"]
    cur = f["current_views"]
    hs = f["horizons"]
    assert [h["days"] for h in hs] == list(HORIZONS)
    for h in hs:
        assert cur <= h["p10"] <= h["p50"] <= h["p90"]
    p50 = [h["p50"] for h in hs]
    assert p50 == sorted(p50) and f["lifetime"]["p50"] >= p50[-1] * 0.999
    g = f["grid"]
    assert g["age_days"][g["now_index"]] == pytest.approx(r["video"]["age_days"], abs=1e-3)
    assert g["p50"][g["now_index"]] == pytest.approx(cur, rel=1e-3)
    for lo, mid, hi in zip(g["p10"], g["p50"], g["p90"]):
        assert lo <= mid + 1e-6 and mid <= hi + 1e-6
    assert all(b >= a for a, b in zip(g["p50"], g["p50"][1:]))
    assert 0 <= r["score"]["value"] <= 100
    assert r["meta"]["confidence"] in {"high", "medium", "low"}
    assert len(f["daily"]) == 14 and all(d["views"] >= 0 for d in f["daily"])


def test_report_is_deterministic(demo_bundle):
    a = build_report(demo_bundle, n_draws=150)
    b = build_report(demo_bundle, n_draws=150)
    assert a["forecast"]["horizons"] == b["forecast"]["horizons"]


def test_everything_is_json_serialisable_and_finite(demo_bundle):
    import json

    text = json.dumps(build_report(demo_bundle, n_draws=100), allow_nan=False)
    assert "NaN" not in text and "Infinity" not in text


def test_history_activates_live_model(now):
    with_hist = build_report(DemoProvider().fetch("demoShort25", now=now), n_draws=100)
    b = DemoProvider().fetch("demoShort25", now=now)
    b.history = None
    without = build_report(b, n_draws=100)
    assert with_hist["model"]["live"] is not None and without["model"]["live"] is None
    assert without["model"]["name"] == "cohort-weibull"


def test_stored_snapshots_feed_the_live_model(demo_bundle):
    b = replace(demo_bundle, history=None, source="youtube")
    age = b.video.age_days(b.fetched_at)
    stored = [(age * f, b.video.views * (1 - math.exp(-4 * f)) / (1 - math.exp(-4))) for f in (0.2, 0.4, 0.6, 0.8)]
    r = build_report(b, stored, n_draws=100)
    assert r["model"]["live"] is not None and r["model"]["live"]["points"] >= 5


def test_zero_views_uses_channel_baseline(demo_bundle):
    b = replace(demo_bundle, video=replace(demo_bundle.video, views=0, likes=0, comments=0))
    r = build_report(b, n_draws=100)
    assert r["meta"]["baseline_only"] and r["forecast"]["current_views"] > 0
    assert any("no views yet" in i["text"] for i in r["insights"])


def test_zero_views_and_no_peers_is_a_clear_error(demo_bundle):
    b = replace(demo_bundle, video=replace(demo_bundle.video, views=0), peers=[], history=None)
    with pytest.raises(AnalysisError):
        build_report(b)


def test_few_peers_lowers_confidence(demo_bundle):
    r = build_report(replace(demo_bundle, peers=demo_bundle.peers[:4]), n_draws=100)
    assert r["meta"]["confidence"] == "low" and r["meta"]["peers_used"] == 4
    assert any("Only 4 recent Shorts" in i["text"] for i in r["insights"])


def test_hidden_likes_and_subscribers_are_handled(demo_bundle):
    v = replace(demo_bundle.video, likes=None, comments=None)
    ch = replace(demo_bundle.channel, subscribers=None)
    r = build_report(replace(demo_bundle, video=v, channel=ch), n_draws=100)
    assert r["signals"]["like_rate"] is None and r["signals"]["reach_ratio"] is None
    assert "reach" not in r["score"]["components"]
    assert any("hides like counts" in i["text"] for i in r["insights"])


def test_a_video_far_ahead_of_its_channel_scores_higher_than_one_far_behind(now):
    hi = build_report(DemoProvider().fetch("demoShort25", now=now), n_draws=100)
    lo = build_report(DemoProvider().fetch("demoShort22", now=now), n_draws=100)
    assert hi["signals"]["performance_index"] > 2 > 0.5 > lo["signals"]["performance_index"]
    assert hi["score"]["value"] > lo["score"]["value"]


def test_old_videos_forecast_a_plateau(now):
    b = DemoProvider().fetch("demoShort25", now=now, target_age_days=90)
    r = build_report(b, n_draws=100)
    h30 = next(h for h in r["forecast"]["horizons"] if h["days"] == 30)
    assert h30["p50"] / r["forecast"]["current_views"] < 1.05


def test_next_round_numbers():
    assert next_round_numbers(184_158) == [200_000, 500_000, 1_000_000]
    assert next_round_numbers(0) == [1, 2, 5]
    assert next_round_numbers(1_000) == [2_000, 5_000, 10_000]


def test_past_is_shown_as_observed_when_history_exists_and_reconstructed_otherwise(now):
    b = DemoProvider().fetch("demoShort25", now=now)
    with_hist = build_report(b, n_draws=100)
    g = with_hist["forecast"]["grid"]
    i = g["now_index"]
    assert with_hist["meta"]["past_observed"]
    assert all(g["p10"][j] == g["p50"][j] == g["p90"][j] for j in range(i))  # no uncertainty about the past
    # the drawn past passes through the recorded points
    for pt in with_hist["history"][1:-1]:
        drawn = np.interp(pt["age_days"], g["age_days"][: i + 1], g["p50"][: i + 1])
        assert abs(drawn - pt["views"]) / pt["views"] < 0.05

    b.history = None
    recon = build_report(b, n_draws=100)["forecast"]["grid"]
    assert not build_report(b, n_draws=100)["meta"]["past_observed"]
    assert any(recon["p90"][j] > recon["p10"][j] for j in range(1, recon["now_index"]))
