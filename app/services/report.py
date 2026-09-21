"""Pure analysis: ``build_report(bundle) -> dict``.

No network, no database, no Flask. Everything the API returns is assembled
here, which keeps it easy to test and to reuse from scripts (see
``scripts/backtest.py``).
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.interpolate import PchipInterpolator

from ..core.cohort import PRIOR_BETA, PRIOR_TAU, CohortModel, weibull_cdf
from ..core.live import fit_live_logistic
from ..core.scoring import momentum, percentile_rank
from ..core.sentiment import analyze_comments
from ..providers.base import Bundle

MODEL_VERSION = "1.0.0"
HORIZONS = (1, 3, 7, 14, 30, 90)
FORECAST_DAYS = 30  # how far the growth surface extends
DAILY_DAYS = 14
LIVE_WEIGHT = 0.25  # hedge toward the video's own trajectory when we have one
MIN_AGE_DAYS = 1.0 / 24.0


class AnalysisError(Exception):
    status = 422
    code = "cannot_analyze"


# -- small helpers -------------------------------------------------------------
def _num(x: Any, digits: int = 2) -> Optional[float]:
    if x is None:
        return None
    x = float(x)
    return round(x, digits) if math.isfinite(x) else None


def _int(x: Any) -> Optional[int]:
    if x is None:
        return None
    x = float(x)
    return int(round(x)) if math.isfinite(x) else None


def compact(n: float) -> str:
    n = float(n)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            v = n / div
            return f"{v:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{n:.0f}"


def _seed(video_id: str) -> int:
    return int.from_bytes(hashlib.sha256(video_id.encode()).digest()[:4], "big")


def next_round_numbers(value: float, count: int = 3) -> List[int]:
    """The next ``count`` values of the form 1, 2 or 5 x 10^k above ``value``."""
    out: List[int] = []
    k = max(0, int(math.floor(math.log10(max(value, 1.0)))) - 1)
    while len(out) < count:
        for m in (1, 2, 5):
            cand = m * 10**k
            if cand > value:
                out.append(cand)
                if len(out) == count:
                    break
        k += 1
    return out


def merge_history(
    provided: Optional[Sequence[Tuple[float, float]]],
    stored: Sequence[Tuple[float, float]],
    age: float,
    views: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """Combine provider history, stored snapshots and the current reading."""
    pts: Dict[float, float] = {}
    for a, v in list(provided or []) + list(stored):
        pts[round(float(a), 5)] = float(v)
    pts[round(float(age), 5)] = float(views)
    items = sorted(pts.items())
    ages = np.array([a for a, _ in items])
    vals = np.maximum.accumulate(np.array([v for _, v in items]))
    return ages, vals


# -- main entry point -----------------------------------------------------------
def build_report(
    bundle: Bundle,
    stored_history: Sequence[Tuple[float, float]] = (),
    *,
    n_draws: int = 400,
    tau_dispersion: Optional[float] = None,
    beta_dispersion: Optional[float] = None,
) -> Dict[str, Any]:
    now = bundle.fetched_at
    v = bundle.video
    ch = bundle.channel
    age = max(v.age_days(now), MIN_AGE_DAYS)
    peers = [p for p in bundle.peers if p.video_id != v.video_id]
    peer_ages = np.array([p.age_days(now) for p in peers], dtype=float)
    peer_views = np.array([p.views for p in peers], dtype=float)

    kwargs: Dict[str, Any] = {}
    if tau_dispersion is not None:
        kwargs["tau_dispersion"] = tau_dispersion
    if beta_dispersion is not None:
        kwargs["beta_dispersion"] = beta_dispersion
    model = CohortModel(peer_ages, peer_views, n_draws=n_draws, seed=_seed(v.video_id), **kwargs)

    # A video with zero views has no trajectory to extend: fall back to the
    # channel's typical Short at this age and say so.
    views = float(v.views)
    baseline = False
    if views < 1:
        expected = model.expected_views(age)
        if expected is None or expected < 1:
            raise AnalysisError(
                "This video has no views yet and its channel has too few recent Shorts to estimate a baseline."
            )
        views, baseline = expected, True

    # -- the video's own trajectory (if we have one) -----------------------------
    h_ages, h_views = merge_history(bundle.history, stored_history, age, views)
    live = fit_live_logistic(h_ages, h_views) if len(h_ages) >= 4 else None

    def forecast_at(times: Sequence[float]) -> Dict[str, np.ndarray]:
        """Cohort bands, nudged toward the live model for future times."""
        t = np.asarray(times, dtype=float)
        bands = model.bands(age, views, t)
        if live is not None:
            future = t > age + 1e-9
            lv = np.full(t.shape, views)
            fin = future & np.isfinite(t)
            if fin.any():
                order = np.argsort(t[fin])
                vals = np.empty(order.size)
                vals[order] = live.forecast(age, views, t[fin][order])
                lv[fin] = vals
            lv[future & ~np.isfinite(t)] = max(live.k, views)
            adj = np.ones_like(t)
            adj[future] = (np.maximum(lv[future], 1e-9) / np.maximum(bands["p50"][future], 1e-9)) ** LIVE_WEIGHT
            bands = {k: arr * adj for k, arr in bands.items()}
        # Cumulative views cannot fall over time and cannot drop below today's count.
        return {k: np.maximum.accumulate(np.maximum(arr, 0.0)) for k, arr in bands.items()}

    # Growth surface grid: reconstructed past + 30 days ahead. Points are spaced evenly in
    # sqrt(time), which is how the charts draw the axis, so the steep first days stay smooth.
    t_max = age + FORECAST_DAYS
    base = t_max * np.linspace(0.0, 1.0, 97) ** 2
    base = base[np.abs(base - age) > 1e-3]
    grid = np.unique(np.concatenate([base, [age]]))
    now_idx = int(np.argmin(np.abs(grid - age)))
    gb = forecast_at(grid)
    for k in gb:  # exact at "now"
        gb[k][now_idx:] = np.maximum(gb[k][now_idx:], views)

    # Where the past was actually observed, show the observation (no uncertainty). Only when
    # there is no recorded history is the past reconstructed from the channel's curve.
    past_observed = len(h_ages) >= 4 and v.views >= 1
    if past_observed:
        xs = np.concatenate([[0.0], h_ages]) if h_ages[0] > 1e-9 else h_ages
        ys = np.concatenate([[0.0], h_views]) if h_ages[0] > 1e-9 else h_views
        observed = PchipInterpolator(xs, ys)(grid[:now_idx])
        for k in gb:
            gb[k][:now_idx] = observed

    hz_times = np.array([age + h for h in HORIZONS] + [np.inf])
    hb = forecast_at(hz_times)
    horizons = []
    for i, h in enumerate(HORIZONS):
        p10, p50, p90 = (max(float(hb[k][i]), views) for k in ("p10", "p50", "p90"))
        horizons.append(
            {
                "days": h,
                "p10": _int(p10),
                "p50": _int(p50),
                "p90": _int(p90),
                "growth_pct": _num((p50 / views - 1.0) * 100.0, 1) if views > 0 else None,
            }
        )
    lifetime = {k: _int(max(float(hb[k][-1]), views)) for k in ("p10", "p50", "p90")}

    daily_times = age + np.arange(0, DAILY_DAYS + 1, dtype=float)
    db = forecast_at(daily_times)
    daily = [
        {
            "day": d,
            "cumulative": _int(db["p50"][d]),
            "views": _int(db["p50"][d] - db["p50"][d - 1]),
            "views_p10": _int(max(db["p10"][d] - db["p10"][d - 1], 0)),
            "views_p90": _int(db["p90"][d] - db["p90"][d - 1]),
        }
        for d in range(1, DAILY_DAYS + 1)
    ]

    # -- milestones --------------------------------------------------------------
    milestones = []
    tf, pf = grid[now_idx:], gb["p50"][now_idx:]
    for target in next_round_numbers(views, 3):
        if lifetime["p90"] is not None and lifetime["p90"] < target:
            status, eta = "unlikely", None
        elif pf[-1] >= target:
            status, eta = "eta", float(np.interp(target, pf, tf)) - age
        else:
            status, eta = "later", None
        milestones.append({"target": target, "status": status, "eta_days": _num(eta, 2)})

    # -- signals -----------------------------------------------------------------
    likes, comments = v.likes, v.comments
    like_rate = likes / v.views if likes is not None and v.views > 0 else None
    comment_rate = comments / v.views if comments is not None and v.views > 0 else None
    engagement = (likes + (comments or 0)) / v.views if likes is not None and v.views > 0 else None

    def peer_engagement(p) -> Optional[float]:
        return (p.likes + (p.comments or 0)) / p.views if p.likes is not None and p.views > 0 else None

    peer_eng = [e for e in (peer_engagement(p) for p in peers) if e is not None]
    peer_like_rates = [p.likes / p.views for p in peers if p.likes is not None and p.views > 0]
    engagement_pct = percentile_rank(engagement, peer_eng) if engagement is not None else None

    expected_now = model.expected_views(age)
    perf_index = views / expected_now if expected_now else None
    perf_pct = percentile_rank(perf_index, model.peer_performance()) if perf_index is not None else None
    reach_ratio = v.views / ch.subscribers if ch.subscribers else None
    sentiment = analyze_comments(bundle.comments)

    score, tier, components = momentum(
        perf_pct, engagement_pct, reach_ratio, sentiment["mean"] if sentiment else None
    )

    views_last_24h = None
    if h_ages[0] <= age - 1.0 and len(h_ages) >= 2:
        views_last_24h = max(v.views - float(np.interp(age - 1.0, h_ages, h_views)), 0.0)

    fit = model.fit
    tau_used = fit.tau if fit else PRIOR_TAU
    beta_used = fit.beta if fit else PRIOR_BETA
    curve_share = {f"day{d}": _num(float(weibull_cdf(d, tau_used, beta_used)), 3) for d in (1, 3, 7, 30)}

    # -- confidence ----------------------------------------------------------------
    i7 = HORIZONS.index(7)
    spread = math.log(max(hb["p90"][i7], 1.0) / max(hb["p10"][i7], 1.0))
    confidence = "high" if spread < 0.35 else "medium" if spread < 0.8 else "low"
    if len(peers) < 15 and confidence == "high":
        confidence = "medium"
    if len(peers) < 8:
        confidence = "low"

    report: Dict[str, Any] = {
        "meta": {
            "source": bundle.source,
            "generated_at": now.astimezone(timezone.utc).isoformat(),
            "model_version": MODEL_VERSION,
            "peers_used": len(peers),
            "confidence": confidence,
            "baseline_only": baseline,
            "past_observed": bool(past_observed),
        },
        "video": {
            "id": v.video_id,
            "url": f"https://www.youtube.com/shorts/{v.video_id}",
            "title": v.title,
            "channel_title": v.channel_title,
            "published_at": v.published_at.astimezone(timezone.utc).isoformat(),
            "age_hours": _num(age * 24, 1),
            "age_days": _num(age, 3),
            "duration_s": v.duration_s,
            "is_short": bundle.is_short,
            "thumbnail": v.thumbnail_url,
            "views": v.views,
            "likes": likes,
            "comments": comments,
        },
        "channel": {
            "id": ch.channel_id,
            "title": ch.title,
            "subscribers": ch.subscribers,
            "total_views": ch.total_views,
            "video_count": ch.video_count,
        },
        "signals": {
            "like_rate": _num(like_rate, 4),
            "comment_rate": _num(comment_rate, 5),
            "engagement_rate": _num(engagement, 4),
            "channel_median_like_rate": _num(float(np.median(peer_like_rates)), 4) if peer_like_rates else None,
            "views_per_hour": _num(v.views / (age * 24.0), 1),
            "views_last_24h": _int(views_last_24h),
            "reach_ratio": _num(reach_ratio, 2),
            "expected_views_now": _int(expected_now),
            "performance_index": _num(perf_index, 2),
            "performance_percentile": _num(perf_pct, 3),
            "engagement_percentile": _num(engagement_pct, 3),
            "sentiment": sentiment,
        },
        "score": {"value": score, "tier": tier, "components": components},
        "forecast": {
            "current_views": _int(views),
            "horizons": horizons,
            "lifetime": lifetime,
            "daily": daily,
            "milestones": milestones,
            "curve_share": curve_share,
            "grid": {
                "age_days": [_num(x, 4) for x in grid],
                "p10": [_num(x, 1) for x in gb["p10"]],
                "p50": [_num(x, 1) for x in gb["p50"]],
                "p90": [_num(x, 1) for x in gb["p90"]],
                "now_index": now_idx,
            },
        },
        "model": {
            "name": "cohort-weibull + live-logistic-rk4" if live else "cohort-weibull",
            "cohort": {
                "tau_days": _num(tau_used, 3),
                "beta": _num(beta_used, 3),
                "peer_spread_log": _num(fit.resid_std, 2) if fit else None,
                "prior_only": model.prior_only,
            },
            "live": (
                {
                    "r_per_day": _num(live.r, 3),
                    "saturation_views": _int(live.k),
                    "rmse_log": _num(live.rmse_log, 3),
                    "points": live.n,
                    "span_days": _num(live.span_days, 2),
                    "weight": LIVE_WEIGHT,
                }
                if live
                else None
            ),
        },
        "history": [
            {"age_days": _num(a, 4), "views": _int(x)}
            for a, x in list(zip(h_ages, h_views))[-200:]
        ],
        "peers": _peer_rows(peers, peer_ages, model, now),
    }
    report["insights"] = build_insights(report)
    return report


def _peer_rows(peers, peer_ages, model: CohortModel, now: datetime) -> List[Dict[str, Any]]:
    perf = model.peer_performance() if peers else []
    rows = []
    for p, a, ratio in zip(peers, peer_ages, perf):
        rows.append(
            {
                "id": p.video_id,
                "url": f"https://www.youtube.com/shorts/{p.video_id}",
                "title": p.title[:90],
                "age_days": _num(a, 3),
                "views": p.views,
                "likes": p.likes,
                "comments": p.comments,
                "performance_index": _num(ratio, 2) if model.fit else None,
            }
        )
    rows.sort(key=lambda r: r["age_days"])
    return rows[:60]


# -- plain-language insights -----------------------------------------------------
def build_insights(r: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    sig, fc, meta, video = r["signals"], r["forecast"], r["meta"], r["video"]

    def add(tone: str, text: str) -> None:
        out.append({"tone": tone, "text": text})

    if meta["baseline_only"]:
        add("warn", "This video has no views yet, so the forecast shows a typical Short from this channel instead.")

    perf = sig["performance_index"]
    if perf is not None:
        pct = sig["performance_percentile"]
        rank = f", ahead of {round(pct * 100)}% of its recent Shorts" if pct is not None else ""
        if perf >= 1.5:
            add("good", f"Ahead of pace: {perf:.1f}x the views a typical Short from this channel has at this age{rank}.")
        elif perf >= 0.8:
            add("info", f"On pace with the channel's typical Short ({perf:.1f}x at this age){rank}.")
        else:
            add("warn", f"Behind pace: {perf:.1f}x the views a typical Short from this channel has at this age{rank}.")

    h7 = next(h for h in fc["horizons"] if h["days"] == 7)
    if h7["growth_pct"] is not None and h7["p50"] is not None:
        add(
            "info",
            f"Expect about {compact(h7['p50'])} views after 7 more days (likely range {compact(h7['p10'])} to {compact(h7['p90'])}).",
        )
    lt = fc["lifetime"]
    if lt["p50"] is not None:
        share = fc["current_views"] / lt["p50"] if lt["p50"] else 1.0
        if share >= 0.98:
            add("info", "Growth has mostly plateaued; little further gain is expected.")
        else:
            add("info", f"Projected lifetime views: about {compact(lt['p50'])}. It has collected roughly {round(share * 100)}% of that so far.")

    for m in fc["milestones"]:
        if m["status"] == "eta" and m["eta_days"] is not None:
            d = m["eta_days"]
            when = f"{d * 24:.0f} hours" if d < 1.5 else f"{d:.1f} days"
            add("good", f"Likely to pass {compact(m['target'])} views in about {when}.")
            break

    if sig["reach_ratio"] is not None and sig["reach_ratio"] >= 1.0:
        add("good", f"Views are {sig['reach_ratio']:.1f}x the subscriber count, which suggests the Short is reaching beyond the existing audience.")

    if sig["like_rate"] is not None and sig["channel_median_like_rate"]:
        med = sig["channel_median_like_rate"]
        tone = "good" if sig["like_rate"] >= med * 1.1 else "warn" if sig["like_rate"] < med * 0.8 else "info"
        add(tone, f"Like rate is {sig['like_rate'] * 100:.1f}% against a channel median of {med * 100:.1f}%.")

    s = sig["sentiment"]
    if s:
        add("good" if s["positive"] >= 0.6 else "warn" if s["negative"] >= 0.3 else "info",
            f"Comment sentiment: {round(s['positive'] * 100)}% positive, {round(s['negative'] * 100)}% negative across {s['n']} comments.")

    # -- caveats -----------------------------------------------------------------
    if video["likes"] is None:
        add("warn", "The creator hides like counts, so engagement signals are limited.")
    if video["comments"] is None:
        add("warn", "Comments are turned off for this video.")
    if not video["is_short"]:
        add("warn", f"This video is {video['duration_s']} seconds long, so it may not behave like a Short. Compare with care.")
    if meta["peers_used"] < 8:
        add("warn", f"Only {meta['peers_used']} recent Shorts from this channel were found, so the growth curve leans on typical Shorts behaviour.")
    if video["age_hours"] is not None and video["age_hours"] < 6:
        add("warn", "The video is very new. Early view counts are noisy, so the range is wide.")
    if r["model"]["live"]:
        add("info", f"Blended with a growth curve fitted to {r['model']['live']['points']} tracked view counts for this video.")
    return out
