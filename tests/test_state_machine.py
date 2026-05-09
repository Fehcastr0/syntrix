"""Tests for core/states.py — StateMachine, OperationalState."""

import time
import unittest

from core.events import EventBus, EventType
from core.states import OperationalState, StateMachine, VALID_TRANSITIONS


class TestOperationalState(unittest.TestCase):
    def test_all_states_have_transitions(self):
        for state in OperationalState:
            self.assertIn(state, VALID_TRANSITIONS)

    def test_idle_can_reach_scanning(self):
        self.assertIn(OperationalState.SCANNING, VALID_TRANSITIONS[OperationalState.IDLE])

    def test_safe_mode_only_to_idle(self):
        targets = VALID_TRANSITIONS[OperationalState.SAFE_MODE]
        self.assertEqual(targets, {OperationalState.IDLE})


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.bus.start()
        self.sm = StateMachine(self.bus)

    def tearDown(self):
        self.bus.stop()

    def test_initial_state(self):
        self.assertEqual(self.sm.state, OperationalState.IDLE)

    def test_valid_transition(self):
        result = self.sm.transition(OperationalState.SCANNING, reason="test")
        self.assertTrue(result)
        self.assertEqual(self.sm.state, OperationalState.SCANNING)

    def test_invalid_transition(self):
        result = self.sm.transition(OperationalState.EXECUTING, reason="test")
        self.assertFalse(result)
        self.assertEqual(self.sm.state, OperationalState.IDLE)

    def test_transition_history(self):
        self.sm.transition(OperationalState.SCANNING)
        self.sm.transition(OperationalState.WAITING_CONTEXT)
        self.sm.transition(OperationalState.READY)
        history = self.sm.history
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0].to_state, OperationalState.SCANNING)
        self.assertEqual(history[2].to_state, OperationalState.READY)

    def test_same_state_returns_true(self):
        result = self.sm.transition(OperationalState.IDLE)
        self.assertTrue(result)

    def test_time_in_state(self):
        time.sleep(0.1)
        elapsed = self.sm.time_in_current_state
        self.assertGreater(elapsed, 0.05)

    def test_metrics(self):
        self.sm.transition(OperationalState.SCANNING)
        time.sleep(0.1)
        self.sm.transition(OperationalState.IDLE)
        metrics = self.sm.metrics.snapshot()
        self.assertGreater(metrics["time_in_state"]["scanning"], 0)

    def test_force_state(self):
        self.sm.force_state(OperationalState.RISK_LOCK, reason="emergency")
        self.assertEqual(self.sm.state, OperationalState.RISK_LOCK)

    def test_full_trade_cycle(self):
        self.sm.transition(OperationalState.SCANNING)
        self.sm.transition(OperationalState.WAITING_CONTEXT)
        self.sm.transition(OperationalState.READY)
        self.sm.transition(OperationalState.EXECUTING)
        self.sm.transition(OperationalState.COOLDOWN)
        self.sm.transition(OperationalState.SCANNING)
        self.assertEqual(self.sm.state, OperationalState.SCANNING)
        self.assertEqual(len(self.sm.history), 6)

    def test_safe_mode_from_any(self):
        for state in [OperationalState.SCANNING, OperationalState.READY,
                       OperationalState.EXECUTING, OperationalState.COOLDOWN]:
            sm = StateMachine(self.bus, initial_state=state)
            result = sm.transition(OperationalState.SAFE_MODE)
            self.assertTrue(result, f"Could not enter SAFE_MODE from {state}")

    def test_conditional_transition(self):
        def block_executing(from_s, to_s):
            return to_s != OperationalState.EXECUTING

        self.sm.add_condition(block_executing)
        self.sm.transition(OperationalState.SCANNING)
        self.sm.transition(OperationalState.WAITING_CONTEXT)
        self.sm.transition(OperationalState.READY)
        result = self.sm.transition(OperationalState.EXECUTING)
        self.assertFalse(result)
        self.assertEqual(self.sm.state, OperationalState.READY)

    def test_event_emission(self):
        events_received = []

        def capture(event):
            events_received.append(event.event_type)

        self.bus.subscribe(EventType.STATE_CHANGED, capture)
        self.sm.transition(OperationalState.SCANNING)
        time.sleep(0.3)

        self.assertIn(EventType.STATE_CHANGED, events_received)


if __name__ == "__main__":
    unittest.main()
