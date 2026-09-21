import json

import pytest

from app import create_app
from app.config import Config
from app.providers.base import ProviderError, QuotaExceeded
from app.providers.demo import DemoProvider
from app.services.analyzer import RateLimiter, TTLCache


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json["status"] == "ok" and r.json["mode"] == "demo"


def test_index_page_renders_with_samples_and_no_inline_code(client):
    r = client.get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "demoShort25" in html and 'data-mode="demo"' in html
    assert "<script>" not in html and " style=" not in html and "onclick=" not in html  # CSP-safe


def test_analyze_returns_a_full_report(client):
    r = client.get("/api/analyze?video=demoShort25")
    assert r.status_code == 200
    d = r.json
    assert set(d) >= {"meta", "video", "channel", "signals", "score", "forecast", "model", "history", "peers", "insights"}
    assert d["meta"]["source"] == "demo" and d["video"]["id"] == "demoShort25"
    assert r.headers["Cache-Control"].startswith("private")


def test_analyze_accepts_urls(client):
    r = client.get("/api/analyze", query_string={"video": "https://www.youtube.com/shorts/demoShort25?feature=share"})
    assert r.status_code == 200 and r.json["video"]["id"] == "demoShort25"


@pytest.mark.parametrize("bad", ["", "nope", "https://example.com/watch?v=demoShort25", "x" * 400, "<script>alert(1)</script>"])
def test_invalid_input_is_a_clean_400(client, bad):
    r = client.get("/api/analyze", query_string={"video": bad})
    assert r.status_code == 400 and r.json["error"]["code"] == "invalid_video"


def test_security_headers(client):
    h = client.get("/").headers
    assert "default-src 'self'" in h["Content-Security-Policy"] and "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"


def test_unknown_api_route_is_json(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json["error"]["code"] == "not_found"


def test_rate_limit_returns_429_with_retry_after(tmp_path):
    app = create_app(Config(data_mode="demo", db_path=str(tmp_path / "x.db"), rate_limit_per_min=3))
    c = app.test_client()
    codes = [c.get("/api/analyze?video=demoShort25").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert c.get("/api/analyze?video=demoShort25").headers["Retry-After"] == "60"


class _Failing(DemoProvider):
    def __init__(self, exc):
        self.exc = exc

    def fetch(self, *a, **k):
        raise self.exc


@pytest.mark.parametrize(
    "exc,status,code",
    [(QuotaExceeded("quota"), 503, "quota_exceeded"), (ProviderError("boom"), 502, "upstream_error")],
)
def test_provider_errors_map_to_http(tmp_path, exc, status, code):
    app = create_app(Config(data_mode="demo", db_path=str(tmp_path / "x.db"), rate_limit_per_min=0), provider=_Failing(exc))
    r = app.test_client().get("/api/analyze?video=demoShort25")
    assert r.status_code == status and r.json["error"]["code"] == code


def test_unexpected_errors_do_not_leak_details(tmp_path):
    app = create_app(Config(data_mode="demo", db_path=str(tmp_path / "x.db"), rate_limit_per_min=0), provider=_Failing(RuntimeError("secret internals")))
    app.config["PROPAGATE_EXCEPTIONS"] = False
    r = app.test_client().get("/api/analyze?video=demoShort25")
    assert r.status_code == 500 and "secret internals" not in json.dumps(r.json)


def test_live_mode_requires_key():
    with pytest.raises(ValueError):
        Config(data_mode="live").validate()
    assert Config(youtube_api_key="k").mode == "live" and Config().mode == "demo"


def test_ttl_cache_expires_and_evicts():
    clock = [0.0]
    c = TTLCache(ttl=10, max_items=2, clock=lambda: clock[0])
    c.set("a", 1); c.set("b", 2); c.set("c", 3)
    assert c.get("a") is None and c.get("b") == 2  # oldest evicted
    clock[0] = 11
    assert c.get("b") is None  # expired


def test_rate_limiter_window():
    clock = [0.0]
    rl = RateLimiter(2, window_s=60, clock=lambda: clock[0])
    assert rl.allow("ip") and rl.allow("ip") and not rl.allow("ip") and rl.allow("other")
    clock[0] = 61
    assert rl.allow("ip")
