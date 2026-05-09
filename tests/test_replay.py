"""Tests for replay/engine.py — ReplayEngine."""

import time
import unittest

from replay.engine import ReplayEngine


class TestReplayEngine(unittest.TestCase):
    def setUp(self):
        self.engine = ReplayEngine()
        self.sample_events = [
            {
                "event_id": "e1",
                "event_type": "system.start",
                "data": '{"msg": "started"}',
                "timestamp": 1000.0,
                "correlation_id": "c1",
                "causation_id": None,
            },
            {
                "event_id": "e2",
                "event_type": "state.changed",
                "data": '{"from": "idle", "to": "scanning"}',
                "timestamp": 1001.0,
                "correlation_id": "c1",
                "causation_id": "e1",
            },
            {
                "event_id": "e3",
                "event_type": "execution.trade_executed",
                "data": '{"asset": "EURUSD"}',
                "timestamp": 1005.0,
                "correlation_id": "c1",
                "causation_id": "e2",
            },
        ]

    def test_build_session_replay(self):
        session = self.engine.build_session_replay(self.sample_events, "test-session")
        self.assertEqual(session.session_id, "test-session")
        self.assertEqual(len(session.events), 3)
        self.assertEqual(session.trade_count, 1)
        self.assertEqual(session.state_changes, 1)
        self.assertGreater(session.duration_sec, 0)

    def test_empty_events(self):
        session = self.engine.build_session_replay([], "empty")
        self.assertEqual(len(session.events), 0)
        self.assertEqual(session.trade_count, 0)

    def test_relative_timing(self):
        session = self.engine.build_session_replay(self.sample_events)
        self.assertEqual(session.events[0].relative_time_ms, 0.0)
        self.assertGreater(session.events[1].relative_time_ms, 0)

    def test_causal_chain(self):
        chain = self.engine.build_causal_chain(self.sample_events, "e1")
        self.assertEqual(len(chain), 3)
        self.assertEqual(chain[0].event_id, "e1")

    def test_trade_replay(self):
        session = self.engine.build_trade_replay(self.sample_events, "c1")
        self.assertEqual(len(session.events), 3)

    def test_replay_callback(self):
        session = self.engine.build_session_replay(self.sample_events)
        collected = []

        def callback(event, index, total):
            collected.append((event.event_type, index))

        self.engine.replay_with_callback(session, callback)
        self.assertEqual(len(collected), 3)

    def test_event_type_distribution(self):
        session = self.engine.build_session_replay(self.sample_events)
        dist = self.engine.get_event_type_distribution(session)
        self.assertIn("system.start", dist)
        self.assertEqual(dist["system.start"], 1)

    def test_timeline(self):
        session = self.engine.build_session_replay(self.sample_events)
        timeline = self.engine.get_timeline(session)
        self.assertEqual(len(timeline), 3)
        self.assertIn("time_ms", timeline[0])


if __name__ == "__main__":
    unittest.main()
