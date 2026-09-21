import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.providers.demo import DemoProvider  # noqa: E402


@pytest.fixture()
def now():
    return datetime.now(timezone.utc)


@pytest.fixture()
def demo_bundle(now):
    return DemoProvider().fetch("demoShort25", now=now)


@pytest.fixture()
def app(tmp_path):
    cfg = Config(data_mode="demo", db_path=str(tmp_path / "t.db"), rate_limit_per_min=0)
    return create_app(cfg)


@pytest.fixture()
def client(app):
    return app.test_client()
