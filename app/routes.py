"""HTTP routes: one page and a small JSON API."""
from __future__ import annotations

import logging
from typing import Tuple

from flask import Blueprint, current_app, jsonify, render_template, request

from . import __version__
from .providers.base import ProviderError
from .providers.youtube import extract_video_id
from .services.report import AnalysisError

log = logging.getLogger(__name__)
bp = Blueprint("main", __name__)

# Demo ids give a spread of outcomes (breakout, steady, slow) without an API key.
DEMO_SAMPLES = ["demoShort25", "demoShort68", "demoShort22"]

CSP = (
    "default-src 'self'; img-src 'self' data: https://i.ytimg.com https://*.ytimg.com; "
    "style-src 'self'; script-src 'self'; font-src 'self'; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def _mode() -> str:
    return "live" if current_app.extensions["mode"] == "youtube" else "demo"


def _error(status: int, code: str, message: str):
    resp = jsonify({"error": {"code": code, "message": message}})
    resp.status_code = status
    return resp


@bp.get("/")
def index():
    mode = _mode()
    return render_template("index.html", mode=mode, samples=DEMO_SAMPLES if mode == "demo" else [], version=__version__)


@bp.get("/api/health")
def health():
    return jsonify({"status": "ok", "mode": _mode(), "version": __version__})


@bp.get("/api/analyze")
def analyze():
    if not current_app.extensions["limiter"].allow(request.remote_addr or "unknown"):
        resp = _error(429, "rate_limited", "Too many requests. Wait a minute and try again.")
        resp.headers["Retry-After"] = "60"
        return resp
    raw = request.args.get("video", "")
    video_id = extract_video_id(raw) if len(raw) <= 300 else None
    if not video_id:
        return _error(400, "invalid_video", "Paste a YouTube Shorts link or an 11-character video id.")
    report = current_app.extensions["analyzer"].analyze(video_id)
    resp = jsonify(report)
    resp.headers["Cache-Control"] = "private, max-age=60"
    return resp


@bp.get("/api/history/<video_id>")
def history(video_id: str):
    vid = extract_video_id(video_id)
    if not vid:
        return _error(400, "invalid_video", "Invalid video id.")
    store = current_app.extensions["store"]
    rows = store.history(vid) if store else []
    return jsonify({"video_id": vid, "snapshots": [{"ts": ts, "views": v, "likes": l, "comments": c} for ts, v, l, c in rows]})


@bp.app_errorhandler(ProviderError)
def _provider_error(exc: ProviderError):
    return _error(exc.status, exc.code, str(exc))


@bp.app_errorhandler(AnalysisError)
def _analysis_error(exc: AnalysisError):
    return _error(exc.status, exc.code, str(exc))


@bp.app_errorhandler(404)
def _not_found(_exc):
    if request.path.startswith("/api/"):
        return _error(404, "not_found", "Unknown endpoint.")
    return "Not found", 404


@bp.app_errorhandler(500)
def _server_error(exc):
    log.exception("Unhandled error: %s", exc)
    if request.path.startswith("/api/"):
        return _error(500, "server_error", "Something went wrong on the server.")
    return "Server error", 500


@bp.after_app_request
def _security_headers(resp):
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return resp
