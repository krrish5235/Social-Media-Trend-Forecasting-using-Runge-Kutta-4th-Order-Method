"""YouTube Data API v3 provider.

Uses plain REST calls (no heavy client library). Quota cost per analysis is
about 5-8 units out of the free 10,000/day: 1 for the video, 1 for the channel,
1-3 for the uploads playlist, 1-3 for peer statistics and 1 for comments.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlparse

import requests

from .base import (
    Bundle,
    ChannelInfo,
    ProviderError,
    QuotaExceeded,
    VideoInfo,
    VideoNotFound,
)

log = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def extract_video_id(text: str) -> Optional[str]:
    """Return the 11-character video id from a URL or bare id, else None."""
    if not text:
        return None
    text = text.strip()
    if _ID_RE.match(text):
        return text
    try:
        url = urlparse(text if "://" in text else "https://" + text)
    except ValueError:
        return None
    host = (url.hostname or "").lower()
    candidate: Optional[str] = None
    if host == "youtu.be":
        parts = [p for p in url.path.split("/") if p]
        candidate = parts[0] if parts else None
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        parts = [p for p in url.path.split("/") if p]
        if parts and parts[0] in {"shorts", "embed", "live", "v"} and len(parts) > 1:
            candidate = parts[1]
        elif parts and parts[0] == "watch":
            candidate = (parse_qs(url.query).get("v") or [None])[0]
    if candidate and _ID_RE.match(candidate):
        return candidate
    return None


def parse_iso_duration(value: str) -> int:
    """ISO-8601 duration ("PT1M5S") -> seconds. Unknown formats give 0."""
    m = _DURATION_RE.match(value or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _opt_int(stats: Dict[str, Any], key: str) -> Optional[int]:
    return int(stats[key]) if key in stats else None


class YouTubeProvider:
    name = "youtube"

    def __init__(
        self,
        api_key: str,
        *,
        session: Optional[requests.Session] = None,
        timeout: float = 10.0,
        max_peers: int = 50,
        shorts_max_seconds: int = 180,
        max_playlist_pages: int = 3,
    ) -> None:
        if not api_key:
            raise ValueError("YOUTUBE_API_KEY is required for the live provider")
        self._key = api_key
        self._session = session or requests.Session()
        self._timeout = timeout
        self.max_peers = max_peers
        self.shorts_max_seconds = shorts_max_seconds
        self.max_playlist_pages = max_playlist_pages

    # -- HTTP -------------------------------------------------------------
    def _get(self, endpoint: str, params: Dict[str, Any], *, retries: int = 2) -> Dict[str, Any]:
        params = {**params, "key": self._key}
        for attempt in range(retries + 1):
            try:
                resp = self._session.get(f"{API_BASE}/{endpoint}", params=params, timeout=self._timeout)
            except requests.RequestException:
                # Never include the exception text: it contains the URL and API key.
                if attempt < retries:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                raise ProviderError("Could not reach the YouTube API. Try again in a moment.")
            if resp.status_code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(0.4 * (attempt + 1))
                continue
            if resp.status_code == 200:
                return resp.json()
            raise self._map_error(resp)
        raise ProviderError("YouTube API request failed")  # pragma: no cover

    @staticmethod
    def _map_error(resp: requests.Response) -> ProviderError:
        reason, message = "", ""
        try:
            err = resp.json().get("error", {})
            message = err.get("message", "")
            reason = (err.get("errors") or [{}])[0].get("reason", "")
        except ValueError:
            pass
        if reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
            return QuotaExceeded("YouTube API quota is used up for today. Try again tomorrow.")
        if reason in {"keyInvalid", "keyExpired"} or "API key not valid" in message:
            return ProviderError(
                "The YouTube API key is invalid. Check YOUTUBE_API_KEY.", status=500, code="bad_api_key"
            )
        if reason == "commentsDisabled":
            return ProviderError("Comments are disabled", status=403, code="comments_disabled")
        if resp.status_code == 404:
            return VideoNotFound("Video not found.")
        log.warning("YouTube API error status=%s reason=%s", resp.status_code, reason)
        return ProviderError(f"YouTube API error ({resp.status_code}).")

    # -- parsing ----------------------------------------------------------
    @staticmethod
    def _to_video(item: Dict[str, Any]) -> VideoInfo:
        sn, st, cd = item.get("snippet", {}), item.get("statistics", {}), item.get("contentDetails", {})
        thumbs = sn.get("thumbnails", {})
        thumb = next((thumbs[k]["url"] for k in ("maxres", "high", "medium", "default") if k in thumbs), None)
        return VideoInfo(
            video_id=item["id"],
            title=sn.get("title", ""),
            channel_id=sn.get("channelId", ""),
            channel_title=sn.get("channelTitle", ""),
            published_at=_parse_time(sn["publishedAt"]),
            duration_s=parse_iso_duration(cd.get("duration", "")),
            views=int(st.get("viewCount", 0)),
            likes=_opt_int(st, "likeCount"),
            comments=_opt_int(st, "commentCount"),
            thumbnail_url=thumb,
        )

    def _videos(self, ids: Sequence[str]) -> List[VideoInfo]:
        out: List[VideoInfo] = []
        for i in range(0, len(ids), 50):
            data = self._get(
                "videos",
                {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i : i + 50]), "maxResults": 50},
            )
            out.extend(self._to_video(it) for it in data.get("items", []))
        return out

    def _channel(self, channel_id: str) -> Tuple[ChannelInfo, str]:
        data = self._get("channels", {"part": "snippet,statistics,contentDetails", "id": channel_id})
        items = data.get("items", [])
        if not items:
            raise ProviderError("Channel not found for this video.", status=404, code="channel_not_found")
        it = items[0]
        st = it.get("statistics", {})
        hidden = bool(st.get("hiddenSubscriberCount"))
        info = ChannelInfo(
            channel_id=channel_id,
            title=it.get("snippet", {}).get("title", ""),
            subscribers=None if hidden or "subscriberCount" not in st else int(st["subscriberCount"]),
            total_views=int(st.get("viewCount", 0)),
            video_count=int(st.get("videoCount", 0)),
        )
        uploads = it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads") or "UU" + channel_id[2:]
        return info, uploads

    def _peers(self, target: VideoInfo, uploads_playlist: str, now: datetime) -> List[VideoInfo]:
        peers: List[VideoInfo] = []
        page_token: Optional[str] = None
        for _ in range(self.max_playlist_pages):
            params: Dict[str, Any] = {"part": "contentDetails", "playlistId": uploads_playlist, "maxResults": 50}
            if page_token:
                params["pageToken"] = page_token
            data = self._get("playlistItems", params)
            ids = [
                it["contentDetails"]["videoId"]
                for it in data.get("items", [])
                if it["contentDetails"]["videoId"] != target.video_id
            ]
            for v in self._videos(ids):
                if v.duration_s <= self.shorts_max_seconds and v.age_days(now) >= 1 / 24:
                    peers.append(v)
            page_token = data.get("nextPageToken")
            if len(peers) >= self.max_peers or not page_token:
                break
        return peers[: self.max_peers]

    def _comments(self, video_id: str) -> List[str]:
        try:
            data = self._get(
                "commentThreads",
                {"part": "snippet", "videoId": video_id, "maxResults": 100, "order": "relevance", "textFormat": "plainText"},
                retries=0,
            )
        except ProviderError as exc:
            if exc.code in {"comments_disabled", "quota_exceeded"} or exc.status in (403, 404):
                return []
            raise
        return [
            it["snippet"]["topLevelComment"]["snippet"].get("textDisplay", "")
            for it in data.get("items", [])
        ]

    # -- public API -------------------------------------------------------
    def fetch(self, video_id: str) -> Bundle:
        now = datetime.now(timezone.utc)
        found = self._videos([video_id])
        if not found:
            raise VideoNotFound("No video found for that link or id.")
        video = found[0]
        channel, uploads = self._channel(video.channel_id)
        peers = self._peers(video, uploads, now)
        comments = self._comments(video_id)
        return Bundle(
            video=video,
            channel=channel,
            peers=peers,
            comments=comments,
            fetched_at=now,
            source="youtube",
            history=None,
            is_short=video.duration_s <= self.shorts_max_seconds,
        )

    def fetch_stats(self, video_ids: Sequence[str]) -> Dict[str, Tuple[int, Optional[int], Optional[int]]]:
        out: Dict[str, Tuple[int, Optional[int], Optional[int]]] = {}
        for i in range(0, len(video_ids), 50):
            data = self._get("videos", {"part": "statistics", "id": ",".join(video_ids[i : i + 50]), "maxResults": 50})
            for it in data.get("items", []):
                st = it.get("statistics", {})
                out[it["id"]] = (int(st.get("viewCount", 0)), _opt_int(st, "likeCount"), _opt_int(st, "commentCount"))
        return out
