from datetime import datetime, timedelta, timezone

import pytest
import requests

from app.providers.base import ProviderError, QuotaExceeded, VideoNotFound
from app.providers.youtube import YouTubeProvider, extract_video_id, parse_iso_duration

VID = "abcdefghijk"


@pytest.mark.parametrize(
    "text,expected",
    [
        (VID, VID),
        (f"https://www.youtube.com/shorts/{VID}", VID),
        (f"https://youtube.com/shorts/{VID}?feature=share", VID),
        (f"https://m.youtube.com/watch?v={VID}&t=5", VID),
        (f"https://youtu.be/{VID}?si=xyz", VID),
        (f"youtube.com/shorts/{VID}", VID),
        (f"https://www.youtube.com/embed/{VID}", VID),
        ("https://evil.com/shorts/" + VID, None),
        ("https://youtube.com.evil.com/watch?v=" + VID, None),
        ("not a link", None),
        ("", None),
        ("javascript:alert(1)", None),
        ("short", None),
    ],
)
def test_extract_video_id(text, expected):
    assert extract_video_id(text) == expected


@pytest.mark.parametrize("value,secs", [("PT45S", 45), ("PT1M", 60), ("PT1M5S", 65), ("PT1H2M3S", 3723), ("P1DT1S", 86401), ("bad", 0), ("", 0)])
def test_parse_iso_duration(value, secs):
    assert parse_iso_duration(value) == secs


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


class FakeSession:
    """Routes YouTube endpoints to canned payloads; records calls."""

    def __init__(self, videos, channel_ok=True, comments=None, errors=None):
        self.videos, self.channel_ok, self.comments, self.errors = videos, channel_ok, comments, errors or {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        ep = url.rsplit("/", 1)[-1]
        self.calls.append((ep, params))
        if ep in self.errors:
            return self.errors[ep]
        if ep == "videos":
            ids = params["id"].split(",")
            return FakeResponse({"items": [self.videos[i] for i in ids if i in self.videos]})
        if ep == "channels":
            return FakeResponse({"items": [{"snippet": {"title": "Chan"}, "statistics": {"subscriberCount": "12345", "viewCount": "999", "videoCount": "70"},
                                            "contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]} if self.channel_ok else {"items": []})
        if ep == "playlistItems":
            ids = [i for i in self.videos]
            return FakeResponse({"items": [{"contentDetails": {"videoId": i}} for i in ids]})
        if ep == "commentThreads":
            return FakeResponse({"items": [{"snippet": {"topLevelComment": {"snippet": {"textDisplay": t}}}} for t in (self.comments or [])]})
        raise AssertionError(ep)


def _item(vid, dur, days_ago, views=1000, likes=50, comments=5, now=None):
    now = now or datetime.now(timezone.utc)
    stats = {"viewCount": str(views)}
    if likes is not None:
        stats["likeCount"] = str(likes)
    if comments is not None:
        stats["commentCount"] = str(comments)
    return {"id": vid, "snippet": {"title": f"t-{vid}", "channelId": "UCxyz", "channelTitle": "Chan", "publishedAt": (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "thumbnails": {"high": {"url": "https://i.ytimg.com/x.jpg"}}},
            "statistics": stats, "contentDetails": {"duration": dur}}


def _videos():
    v = {VID: _item(VID, "PT30S", 3, views=5000)}
    for i in range(12):
        v[f"peerShort{i:02d}"] = _item(f"peerShort{i:02d}", "PT40S", 5 + i * 3)
    v["longFormVid"] = _item("longFormVid", "PT12M", 10)  # not a Short
    v["brandNewOne"] = _item("brandNewOne", "PT20S", 0.001)  # under an hour old
    return v


def test_fetch_builds_bundle_and_filters_peers():
    sess = FakeSession(_videos(), comments=["great!", "meh"])
    b = YouTubeProvider("KEY", session=sess).fetch(VID)
    assert b.video.views == 5000 and b.video.likes == 50 and b.is_short
    assert b.channel.subscribers == 12345
    ids = {p.video_id for p in b.peers}
    assert VID not in ids and "longFormVid" not in ids and "brandNewOne" not in ids
    assert len(b.peers) == 12
    assert b.comments == ["great!", "meh"] and b.source == "youtube"
    assert all(c[1]["key"] == "KEY" for c in sess.calls)


def test_hidden_likes_and_disabled_comments_are_none():
    v = _videos()
    v[VID] = _item(VID, "PT30S", 3, likes=None, comments=None)
    sess = FakeSession(v, errors={"commentThreads": FakeResponse({"error": {"code": 403, "errors": [{"reason": "commentsDisabled"}]}}, 403)})
    b = YouTubeProvider("KEY", session=sess).fetch(VID)
    assert b.video.likes is None and b.video.comments is None and b.comments == []


def test_hidden_subscriber_count():
    sess = FakeSession(_videos())
    orig = sess.get

    def get(url, params=None, timeout=None):
        r = orig(url, params, timeout)
        if url.endswith("channels"):
            r._payload["items"][0]["statistics"]["hiddenSubscriberCount"] = True
        return r

    sess.get = get
    assert YouTubeProvider("KEY", session=sess).fetch(VID).channel.subscribers is None


def test_missing_video_raises_not_found():
    with pytest.raises(VideoNotFound):
        YouTubeProvider("KEY", session=FakeSession({})).fetch(VID)


def test_quota_and_bad_key_errors_are_mapped():
    quota = FakeResponse({"error": {"code": 403, "message": "q", "errors": [{"reason": "quotaExceeded"}]}}, 403)
    with pytest.raises(QuotaExceeded):
        YouTubeProvider("KEY", session=FakeSession(_videos(), errors={"videos": quota})).fetch(VID)
    bad = FakeResponse({"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "errors": [{"reason": "badRequest"}]}}, 400)
    with pytest.raises(ProviderError) as e:
        YouTubeProvider("KEY", session=FakeSession(_videos(), errors={"videos": bad})).fetch(VID)
    assert e.value.code == "bad_api_key" and e.value.status == 500


def test_network_errors_never_leak_the_api_key(monkeypatch):
    class Boom:
        def get(self, url, params=None, timeout=None):
            raise requests.ConnectionError(f"failed for {url}?key={params['key']}")

    monkeypatch.setattr("app.providers.youtube.time.sleep", lambda *_: None)  # no real backoff
    prov = YouTubeProvider("SECRET-KEY-123", session=Boom())
    with pytest.raises(ProviderError) as e:
        prov.fetch(VID)
    assert "SECRET-KEY-123" not in str(e.value)


def test_requires_api_key():
    with pytest.raises(ValueError):
        YouTubeProvider("")


def test_fetch_stats_batches():
    sess = FakeSession(_videos())
    out = YouTubeProvider("KEY", session=sess).fetch_stats([VID, "peerShort00"])
    assert out[VID][0] == 5000 and out["peerShort00"][1] == 50
