"""Tests for shadow mode."""

import os
import pytest
import tempfile
from execution.shadow import ShadowMode, ShadowRecord


class TestShadowMode:
    def test_record_and_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = ShadowMode(data_dir=tmpdir)
            sm.record(ShadowRecord(
                timestamp=1000, asset="EURUSD-OTC",
                direction="call", strategy="momentum",
                score=0.65, payout=0.80,
                decision="EXECUTE",
            ))
            sm.record(ShadowRecord(
                timestamp=1001, asset="GBPUSD-OTC",
                decision="BLOCK", block_reason="LOW_PAYOUT",
            ))
            stats = sm.get_stats()
            assert stats["total_records"] == 2
            assert stats["executed"] == 1
            assert stats["blocked"] == 1
            assert stats["allow_rate"] == 50.0
            assert "LOW_PAYOUT" in stats["block_reasons"]

    def test_csv_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = ShadowMode(data_dir=tmpdir)
            sm.record(ShadowRecord(
                timestamp=1000, asset="TEST",
                decision="EXECUTE",
            ))
            csv_files = [f for f in os.listdir(tmpdir) if f.endswith(".csv")]
            assert len(csv_files) == 1

    def test_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = ShadowMode(data_dir=tmpdir)
            sm.record(ShadowRecord(
                timestamp=1000, decision="EXECUTE",
            ))
            report = sm.get_report()
            assert "SHADOW MODE REPORT" in report
