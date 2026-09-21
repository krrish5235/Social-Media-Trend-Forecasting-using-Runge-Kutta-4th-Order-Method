from datetime import datetime, timedelta, timezone

from app import create_app
from app.config import Config
from app.providers.demo import DemoProvider
from app.services.store import SnapshotStore


def test_record_dedupes_close_readings_and_orders_history(tmp_path):
    s = SnapshotStore(str(tmp_path / "s.db"))
    assert s.record("v", 1000.0, 10, 1, 0)
    assert not s.record("v", 1100.0, 11, 1, 0)  # within 10 minutes
    assert s.record("v", 2000.0, 50, 3, 1)
    assert [(ts, v) for ts, v, *_ in s.history("v")] == [(1000.0, 10), (2000.0, 50)]
    assert s.history("other") == []


def test_tracked_ids_and_prune(tmp_path):
    s = SnapshotStore(str(tmp_path / "s.db"))
    now = 10_000_000.0
    s.record("fresh", now - 86400, 5, None, None)
    s.record("stale", now - 40 * 86400, 5, None, None)
    assert s.tracked_ids(within_days=30, now=now) == ["fresh"]
    assert s.prune(older_than_days=30, now=now) == 1
    assert s.history("stale") == []


class _LiveLike(DemoProvider):
    """Demo data that pretends to be YouTube (no history) so snapshots are stored."""

    name = "youtube"

    def fetch(self, video_id, **kw):
        b = super().fetch(video_id, **kw)
        b.history, b.source = None, "youtube"
        return b


def test_analysis_records_snapshots_and_history_endpoint_serves_them(tmp_path):
    app = create_app(Config(data_mode="demo", db_path=str(tmp_path / "a.db"), rate_limit_per_min=0), provider=_LiveLike())
    c = app.test_client()
    assert c.get("/api/analyze?video=demoShort25").status_code == 200
    rows = c.get("/api/history/demoShort25").json["snapshots"]
    assert len(rows) == 1 and rows[0]["views"] > 0
    assert c.get("/api/analyze?video=demoShort25").status_code == 200  # cached bundle: no duplicate row
    assert len(c.get("/api/history/demoShort25").json["snapshots"]) == 1
    assert c.get("/api/history/bad").status_code == 400
