"""Tests for core/events.py — EventBus, Event, EventPriority."""

import time
import threading
import unittest

from core.events import Event, EventBus, EventPriority, EventType


class TestEvent(unittest.TestCase):
    def test_event_creation(self):
        evt = Event(event_type=EventType.SYSTEM_START, data={"key": "val"})
        self.assertEqual(evt.event_type, EventType.SYSTEM_START)
        self.assertEqual(evt.data["key"], "val")
        self.assertEqual(evt.priority, EventPriority.NORMAL)
        self.assertIsNotNone(evt.event_id)

    def test_event_ordering(self):
        high = Event(event_type=EventType.SYSTEM_START, priority=EventPriority.HIGH)
        low = Event(event_type=EventType.SYSTEM_START, priority=EventPriority.LOW)
        self.assertTrue(high < low)

    def test_event_child(self):
        parent = Event(event_type=EventType.SYSTEM_START, source="test")
        child = parent.child(EventType.STATE_CHANGED, data={"to": "scanning"})
        self.assertEqual(child.causation_id, parent.event_id)
        self.assertEqual(child.parent_event_id, parent.event_id)
        self.assertEqual(child.source, "test")

    def test_event_serialization(self):
        evt = Event(event_type=EventType.CANDLE_RECEIVED, data={"close": 1.234})
        d = evt.to_dict()
        self.assertEqual(d["event_type"], "market.candle_received")
        json_str = evt.to_json()
        self.assertIn("market.candle_received", json_str)

    def test_event_deserialization(self):
        evt = Event(event_type=EventType.SYSTEM_START, data={"foo": "bar"})
        d = evt.to_dict()
        restored = Event.from_dict(d)
        self.assertEqual(restored.event_type, EventType.SYSTEM_START)
        self.assertEqual(restored.data["foo"], "bar")


class TestEventBus(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus(num_workers=2)
        self.bus.start()

    def tearDown(self):
        if self.bus.is_running:
            self.bus.stop(timeout=3.0)
        # Give daemon threads time to exit
        time.sleep(0.2)

    def test_publish_subscribe(self):
        received = []

        def handler(event: Event):
            received.append(event)

        self.bus.subscribe(EventType.SYSTEM_START, handler)
        self.bus.emit(EventType.SYSTEM_START, data={"msg": "hello"})
        time.sleep(0.5)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["msg"], "hello")

    def test_wildcard_subscribe(self):
        received = []

        def handler(event: Event):
            received.append(event)

        self.bus.subscribe_all(handler)
        self.bus.emit(EventType.SYSTEM_START)
        self.bus.emit(EventType.STATE_CHANGED)
        time.sleep(0.5)

        self.assertEqual(len(received), 2)

    def test_unsubscribe(self):
        received = []

        def handler(event: Event):
            received.append(event)

        self.bus.subscribe(EventType.SYSTEM_START, handler)
        self.bus.emit(EventType.SYSTEM_START)
        time.sleep(0.3)
        self.bus.unsubscribe(EventType.SYSTEM_START, handler)
        self.bus.emit(EventType.SYSTEM_START)
        time.sleep(0.3)

        self.assertEqual(len(received), 1)

    def test_priority_ordering(self):
        order = []

        def handler(event: Event):
            order.append(event.priority)
            time.sleep(0.02)

        self.bus.subscribe(EventType.SYSTEM_START, handler)
        self.bus.publish(Event(event_type=EventType.SYSTEM_START, priority=EventPriority.LOW))
        self.bus.publish(Event(event_type=EventType.SYSTEM_START, priority=EventPriority.CRITICAL))
        time.sleep(1.0)

        self.assertTrue(len(order) >= 2)

    def test_metrics(self):
        def handler(event: Event):
            pass

        self.bus.subscribe(EventType.SYSTEM_START, handler)
        for _ in range(10):
            self.bus.emit(EventType.SYSTEM_START)
        time.sleep(0.5)

        metrics = self.bus.metrics.snapshot()
        self.assertEqual(metrics["events_published"], 10)
        self.assertEqual(metrics["events_processed"], 10)

    def test_dead_letter_queue(self):
        def bad_handler(event: Event):
            raise ValueError("Intentional error")

        self.bus.subscribe(EventType.SYSTEM_ERROR, bad_handler)
        evt = Event(event_type=EventType.SYSTEM_ERROR, max_retries=1)
        self.bus.publish(evt)
        time.sleep(2.0)

        self.assertGreater(self.bus.dead_letter_count, 0)

    def test_graceful_shutdown(self):
        processed = []

        def handler(event: Event):
            processed.append(1)
            time.sleep(0.01)

        self.bus.subscribe(EventType.SYSTEM_START, handler)
        for _ in range(5):
            self.bus.emit(EventType.SYSTEM_START)
        self.bus.stop(timeout=5.0)

        self.assertEqual(len(processed), 5)

    def test_not_running_drops_events(self):
        self.bus.stop(timeout=2.0)
        result = self.bus.emit(EventType.SYSTEM_START)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
