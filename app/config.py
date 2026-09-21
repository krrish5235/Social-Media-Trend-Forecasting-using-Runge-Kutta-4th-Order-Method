"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    youtube_api_key: str = ""
    data_mode: str = "auto"  # auto | live | demo
    db_path: str = "data/snapshots.db"
    cache_ttl: int = 900  # seconds a fetched video is reused (saves API quota)
    max_peers: int = 50
    shorts_max_seconds: int = 180  # YouTube allows Shorts up to 3 minutes
    rate_limit_per_min: int = 30
    n_draws: int = 400
    trust_proxy: bool = False  # set when behind a reverse proxy (nginx, Render, ...)

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            youtube_api_key=os.getenv("YOUTUBE_API_KEY", "").strip(),
            data_mode=os.getenv("DATA_MODE", "auto").strip().lower(),
            db_path=os.getenv("DB_PATH", "data/snapshots.db"),
            cache_ttl=int(os.getenv("CACHE_TTL_SECONDS", "900")),
            max_peers=int(os.getenv("MAX_PEERS", "50")),
            shorts_max_seconds=int(os.getenv("SHORTS_MAX_SECONDS", "180")),
            rate_limit_per_min=int(os.getenv("RATE_LIMIT_PER_MIN", "30")),
            n_draws=int(os.getenv("N_DRAWS", "400")),
            trust_proxy=_bool("TRUST_PROXY"),
        )

    @property
    def mode(self) -> str:
        """Effective data mode: 'live' or 'demo'."""
        if self.data_mode == "demo":
            return "demo"
        if self.data_mode == "live":
            return "live"
        return "live" if self.youtube_api_key else "demo"

    def validate(self) -> None:
        if self.data_mode not in {"auto", "live", "demo"}:
            raise ValueError("DATA_MODE must be auto, live or demo")
        if self.mode == "live" and not self.youtube_api_key:
            raise ValueError("DATA_MODE=live requires YOUTUBE_API_KEY")
