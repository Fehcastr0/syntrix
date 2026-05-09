#!/usr/bin/env python3
"""Script to generate analytics report from stored trades."""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.trade_analytics import TradeAnalytics, TradeRecord
from event_store.sqlite_store import SQLiteEventStore


def generate_report(db_path: str = "data/syntrix.db"):
    """Load trades from DB and generate analytics report."""
    store = SQLiteEventStore(db_path=db_path)
    analytics = TradeAnalytics()

    trades = store.get_trades(limit=10000)

    if not trades:
        print("No trades found in database.")
        return

    for t in trades:
        analytics.add_trade(TradeRecord(
            trade_id=t.get("trade_id", ""),
            asset=t.get("asset", ""),
            direction=t.get("direction", ""),
            strategy=t.get("strategy", ""),
            regime=t.get("regime", ""),
            session=t.get("session_name", ""),
            profile=t.get("profile", ""),
            payout=t.get("payout", 0),
            amount=t.get("amount", 0),
            result=t.get("result", ""),
            profit=t.get("profit", 0),
            timestamp=t.get("timestamp_executed", 0),
            score=t.get("score", 0),
        ))

    report = analytics.full_report()

    print(f"\n{'═' * 60}")
    print("  SYNTRIX ANALYTICS REPORT")
    print(f"{'═' * 60}\n")
    print(json.dumps(report, indent=2, default=str))
    print(f"\n{'═' * 60}\n")


if __name__ == "__main__":
    generate_report()
