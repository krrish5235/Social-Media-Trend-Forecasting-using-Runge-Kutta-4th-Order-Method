"""Data types shared by every data provider."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Protocol, Sequence, Tuple


class ProviderError(Exception):
    """Base error for data providers. ``status`` maps to an HTTP status code."""

    status = 502
    code = "upstream_error"

    def __init__(self, message: str, *, status: Optional[int] = None, code: Optional[str] = None):
        super().__init__(message)
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code


class VideoNotFound(ProviderError):
    status = 404
    code = "video_not_found"


class QuotaExceeded(ProviderError):
    status = 503
    code = "quota_exceeded"


@dataclass
class VideoInfo:
    video_id: str
    title: str
    channel_id: str
    channel_title: str
    published_at: datetime  # timezone-aware, UTC
    duration_s: int
    views: int
    likes: Optional[int]  # None when the creator hides likes
    comments: Optional[int]  # None when comments are disabled
    thumbnail_url: Optional[str] = None

    def age_days(self, now: datetime) -> float:
        return max((now - self.published_at).total_seconds() / 86400.0, 0.0)


@dataclass
class ChannelInfo:
    channel_id: str
    title: str
    subscribers: Optional[int]  # None when the creator hides the count
    total_views: int
    video_count: int


@dataclass
class Bundle:
    """Everything the analyzer needs about one video."""

    video: VideoInfo
    channel: ChannelInfo
    peers: List[VideoInfo]  # the channel's other recent Shorts
    comments: List[str]  # sample of top-level comment texts
    fetched_at: datetime
    source: str  # "youtube" | "demo"
    # Optional (age_days, cumulative_views) history supplied by the provider.
    # The public YouTube API has none; demo data and Analytics-API data do.
    history: Optional[List[Tuple[float, float]]] = None
    is_short: bool = True


class Provider(Protocol):
    name: str

    def fetch(self, video_id: str) -> Bundle: ...

    def fetch_stats(self, video_ids: Sequence[str]) -> Dict[str, Tuple[int, Optional[int], Optional[int]]]:
        """Current (views, likes, comments) for many videos, used by the poller."""
        ...
