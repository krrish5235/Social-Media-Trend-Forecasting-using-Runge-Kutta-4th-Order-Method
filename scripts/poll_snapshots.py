"""Record fresh view counts for every video you have analysed recently.

The public YouTube API has no view history, so this script builds it: run it
from cron every few hours and the live-tracking model gets real data to fit.
One API call refreshes up to 50 videos (1 quota unit).

    */3 * * * *  cd /path/to/app && python scripts/poll_snapshots.py

Videos are tracked for ``--days`` (default 14) after they were first analysed.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from app.config import Config  # noqa: E402
from app.providers.youtube import YouTubeProvider  # noqa: E402
from app.services.store import SnapshotStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=14.0, help="keep tracking a video this long after first analysis")
    ap.add_argument("--prune-days", type=float, default=180.0, help="delete readings older than this")
    args = ap.parse_args()

    load_dotenv()
    cfg = Config.from_env()
    if cfg.mode != "live":
        print("Demo mode: nothing to poll. Set YOUTUBE_API_KEY to track real videos.")
        return 0
    store = SnapshotStore(cfg.db_path)
    now = time.time()
    ids = store.tracked_ids(args.days, now)
    if not ids:
        print("No tracked videos yet. Analyse a Short in the app first.")
        return 0
    stats = YouTubeProvider(cfg.youtube_api_key).fetch_stats(ids)
    stored = sum(store.record(v, now, *stats[v]) for v in ids if v in stats)
    pruned = store.prune(args.prune_days, now)
    print(f"Tracked {len(ids)} videos, stored {stored} new readings, pruned {pruned} old ones.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
