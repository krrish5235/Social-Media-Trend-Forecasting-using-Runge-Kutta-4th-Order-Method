"""Synthetic data provider.

Lets the whole app run without a YouTube API key, and gives tests a *known*
ground truth: every video id deterministically maps to a synthetic channel
whose true accumulation curves we can evaluate at any age, so forecasts can be
scored honestly (see ``scripts/backtest.py``).

Numbers here are made up. The UI labels demo results clearly.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from ..core.cohort import weibull_cdf
from .base import Bundle, ChannelInfo, VideoInfo

_TITLES = [
    "Wait for the last second", "I tried this so you don't have to", "Day 30 of learning guitar",
    "This trick saves 2 hours a week", "Rate my setup 1 to 10", "The cat had other plans",
    "Cooking 5 meals for under $10", "POV: your alarm rings once", "Before and after the glow up",
    "3 mistakes every beginner makes", "Trying viral food combos", "How it's made in 30 seconds",
    "Nobody told me about this setting", "Small budget, big transformation", "Unexpected plot twist",
    "Morning routine that actually works", "Testing cheap gadgets", "Street food at midnight",
    "Speedrun to 100 percent", "What happens at 1 million views",
]
_POSITIVE = ["Love this!", "This is amazing, thank you", "Best one so far", "So helpful, saved it",
             "You are so talented", "Made my day", "Great video, subscribed", "Absolutely brilliant"]
_NEUTRAL = ["First", "Who is watching in 2026", "What song is this", "Part 2 please", "Where is this filmed",
            "What camera do you use", "Watching from Brazil", "Can you do this with a slower pace"]
_NEGATIVE = ["Not really impressed", "This is boring", "Clickbait, waste of time", "Terrible audio",
             "Do not recommend", "Awful, unsubscribing", "This makes no sense"]


def _seed(video_id: str) -> int:
    return int.from_bytes(hashlib.sha256(video_id.encode()).digest()[:8], "big")


class World:
    """Ground truth for one synthetic channel + target video."""

    SIGMA_LEVEL = 0.9  # spread of lifetime views between videos
    TAU_SD = 0.20  # per-video variation of the accumulation curve
    BETA_SD = 0.06
    OBS_NOISE = 0.03

    def __init__(self, video_id: str, target_age_days: Optional[float] = None) -> None:
        rng = random.Random(_seed(video_id))
        self.rng = rng
        self.subs = int(10 ** rng.uniform(3.6, 6.4))
        self.tau = math.exp(rng.gauss(math.log(1.3), 0.25))
        self.beta = min(max(rng.gauss(0.6, 0.08), 0.4), 0.9)
        self.m_med = self.subs * math.exp(rng.uniform(math.log(0.1), math.log(1.5)))
        self.like_rate = math.exp(rng.gauss(math.log(0.05), 0.35))
        self.comment_rate = math.exp(rng.gauss(math.log(0.0015), 0.4))
        self.p_positive = rng.uniform(0.35, 0.8)
        self.target_age = target_age_days if target_age_days is not None else rng.uniform(0.3, 7.0)
        self.target = self._draw_video()
        self.peers = [self._peer() for _ in range(rng.randint(18, 46))]

    def _draw_video(self) -> Dict[str, float]:
        r = self.rng
        return {
            "m": self.m_med * math.exp(r.gauss(0.0, self.SIGMA_LEVEL)),
            "tau": self.tau * math.exp(r.gauss(0.0, self.TAU_SD)),
            "beta": min(max(self.beta * math.exp(r.gauss(0.0, self.BETA_SD)), 0.3), 1.2),
            "noise": math.exp(r.gauss(0.0, self.OBS_NOISE)),
            "rate_mult": math.exp(r.gauss(0.0, 0.25)),
        }

    def _peer(self) -> Dict[str, float]:
        r = self.rng
        v = self._draw_video()
        v["age"] = r.uniform(0.3, 60.0) if r.random() < 0.6 else math.exp(r.uniform(math.log(0.2), math.log(5.0)))
        v["title"] = r.choice(_TITLES)
        return v

    @staticmethod
    def views_at(v: Dict[str, float], age: float, noisy: bool = True) -> float:
        val = v["m"] * float(weibull_cdf(age, v["tau"], v["beta"]))
        return val * (v["noise"] if noisy else 1.0)

    def true_views(self, age: float) -> float:
        """Ground-truth cumulative views of the target at ``age`` days.

        Includes the video's persistent noise factor, so that
        ``true_views(target_age)`` equals the view count the provider reports.
        """
        return self.views_at(self.target, age, noisy=True)

    def history_ages(self) -> List[float]:
        grid = [1 / 24, 3 / 24, 6 / 24, 12 / 24, 1, 1.5, 2, 3, 4, 5, 7, 10, 14, 21, 30, 45, 60]
        ages = [a for a in grid if a < self.target_age - 1e-6]
        return ages + [self.target_age]


class DemoProvider:
    name = "demo"

    def fetch(self, video_id: str, now: Optional[datetime] = None, target_age_days: Optional[float] = None) -> Bundle:
        now = now or datetime.now(timezone.utc)
        w = World(video_id, target_age_days)
        rng = random.Random(_seed(video_id) ^ 0x5DEECE66D)

        def to_info(vid: str, title: str, v: Dict[str, float], age: float) -> VideoInfo:
            views = int(round(w.views_at(v, age)))
            return VideoInfo(
                video_id=vid,
                title=title,
                channel_id="UCdemo" + video_id[:6],
                channel_title=f"Demo Channel {video_id[:4].upper()}",
                published_at=now - timedelta(days=age),
                duration_s=rng.choice([14, 22, 31, 38, 47, 58]),
                views=views,
                likes=int(views * w.like_rate * v["rate_mult"]),
                comments=int(views * w.comment_rate * v["rate_mult"]),
                thumbnail_url=None,
            )

        target = to_info(video_id, rng.choice(_TITLES), w.target, w.target_age)
        peers = [
            to_info(f"{video_id[:6]}p{i:03d}", p["title"], p, p["age"]) for i, p in enumerate(w.peers)
        ]
        n_comments = 60
        comments = []
        for _ in range(n_comments):
            u = rng.random()
            if u < w.p_positive:
                comments.append(rng.choice(_POSITIVE))
            elif u < w.p_positive + (1 - w.p_positive) * 0.6:
                comments.append(rng.choice(_NEUTRAL))
            else:
                comments.append(rng.choice(_NEGATIVE))

        hist: List[Tuple[float, float]] = []
        for age in w.history_ages():
            hist.append((age, w.views_at(w.target, age) * math.exp(rng.gauss(0, 0.01))))
        # Cumulative counters never decrease; the last point equals the current view count.
        running = 0.0
        history = []
        for age, v in hist:
            running = max(running, v)
            history.append((age, running))
        history[-1] = (history[-1][0], float(target.views))

        channel = ChannelInfo(
            channel_id=target.channel_id,
            title=target.channel_title,
            subscribers=w.subs,
            total_views=int(sum(p.views for p in peers) + target.views) * 3,
            video_count=len(peers) + 40,
        )
        return Bundle(
            video=target,
            channel=channel,
            peers=peers,
            comments=comments,
            fetched_at=now,
            source="demo",
            history=history,
            is_short=True,
        )

    def fetch_stats(self, video_ids: Sequence[str]) -> Dict[str, Tuple[int, Optional[int], Optional[int]]]:
        return {}
