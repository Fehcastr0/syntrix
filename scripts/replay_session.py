#!/usr/bin/env python3
"""Script to replay a session from the event store."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from event_store.sqlite_store import SQLiteEventStore
from replay.engine import ReplayEngine


def replay(session_id: str = "", db_path: str = "data/syntrix.db"):
    """Replay a session and print timeline."""
    store = SQLiteEventStore(db_path=db_path)
    engine = ReplayEngine()

    events = store.get_events(session_id=session_id or None, limit=10000)

    if not events:
        print("No events found.")
        return

    session = engine.build_session_replay(events, session_id or "all")

    print(f"\n{'═' * 60}")
    print(f"  REPLAY: {session.session_id}")
    print(f"  Events: {len(session.events)}")
    print(f"  Duration: {session.duration_sec:.1f}s")
    print(f"  Trades: {session.trade_count}")
    print(f"  State changes: {session.state_changes}")
    print(f"{'═' * 60}\n")

    # Print event distribution
    dist = engine.get_event_type_distribution(session)
    print("Event distribution:")
    for etype, count in dist.items():
        print(f"  {etype}: {count}")

    print(f"\n{'─' * 60}")
    print("Timeline:")
    for evt in session.events:
        print(f"  [{evt.relative_time_ms:>8.1f}ms] {evt.event_type}")

    print(f"\n{'═' * 60}\n")


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else ""
    replay(session_id=sid)
