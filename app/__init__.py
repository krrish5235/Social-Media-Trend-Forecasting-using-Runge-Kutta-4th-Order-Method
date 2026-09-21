"""Shorts Growth Predictor: Flask application factory."""
from __future__ import annotations

import logging
from typing import Optional

from dotenv import load_dotenv
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .providers.base import Provider
from .providers.demo import DemoProvider
from .providers.youtube import YouTubeProvider
from .services.analyzer import Analyzer, RateLimiter
from .services.store import SnapshotStore

__version__ = "1.0.0"


def make_provider(config: Config) -> Provider:
    if config.mode == "live":
        return YouTubeProvider(
            config.youtube_api_key,
            max_peers=config.max_peers,
            shorts_max_seconds=config.shorts_max_seconds,
        )
    return DemoProvider()


def create_app(config: Optional[Config] = None, provider: Optional[Provider] = None) -> Flask:
    load_dotenv()
    config = config or Config.from_env()
    config.validate()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = Flask(__name__)
    app.json.sort_keys = False
    if config.trust_proxy:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    provider = provider or make_provider(config)
    store = SnapshotStore(config.db_path) if provider.name == "youtube" else None
    app.extensions["config"] = config
    app.extensions["store"] = store
    app.extensions["analyzer"] = Analyzer(provider, store, cache_ttl=config.cache_ttl, n_draws=config.n_draws)
    app.extensions["limiter"] = RateLimiter(config.rate_limit_per_min)
    app.extensions["mode"] = provider.name if provider.name in {"demo", "youtube"} else config.mode

    from .routes import bp

    app.register_blueprint(bp)
    return app
