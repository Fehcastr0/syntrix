"""
config/loader.py — Configuration loader for Syntrix.

Loads profiles.yaml and provides typed configuration objects.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from execution.engine import ExecutionConfig
from risk.risk_engine import RiskConfig

logger = logging.getLogger("syntrix.config")

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "profiles.yaml")


class ConfigLoader:
    """Loads and manages Syntrix configuration from YAML."""

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH) -> None:
        self._path = config_path
        self._data: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Load configuration from YAML file."""
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = yaml.safe_load(f) or {}
            logger.info("Configuration loaded from %s", self._path)
        except FileNotFoundError:
            logger.warning("Config file not found: %s — using defaults", self._path)
            self._data = {}
        except yaml.YAMLError as exc:
            logger.error("YAML parse error in %s: %s", self._path, exc)
            self._data = {}

    @property
    def raw(self) -> Dict[str, Any]:
        return self._data

    def get_defaults(self) -> Dict[str, Any]:
        """Get default settings."""
        return self._data.get("defaults", {})

    def get_default_profile_name(self) -> str:
        """Get the default profile name."""
        return self.get_defaults().get("profile", "leve")

    def get_mode(self) -> str:
        """Get operational mode (dry-run / demo / live)."""
        return self.get_defaults().get("mode", "dry-run")

    def get_assets(self) -> List[str]:
        """Get list of configured assets."""
        return self.get_defaults().get("assets", ["EURUSD-OTC"])

    def list_profiles(self) -> List[str]:
        """List all profile names."""
        return list(self._data.get("profiles", {}).keys())

    def list_active_profiles(self) -> List[str]:
        """List only active profiles."""
        profiles = self._data.get("profiles", {})
        return [name for name, cfg in profiles.items() if cfg.get("active", False)]

    def get_profile(self, name: str) -> Dict[str, Any]:
        """Get raw profile configuration."""
        return self._data.get("profiles", {}).get(name, {})

    def get_risk_config(self, profile_name: Optional[str] = None) -> RiskConfig:
        """Build RiskConfig from a profile."""
        name = profile_name or self.get_default_profile_name()
        profile = self.get_profile(name)
        risk = profile.get("risk", {})

        return RiskConfig(
            stop_gain=risk.get("stop_gain", 50.0),
            stop_loss=risk.get("stop_loss", -30.0),
            max_drawdown_pct=risk.get("max_drawdown_pct", 10.0),
            max_trades_per_hour=risk.get("max_trades_per_hour", 10),
            max_consecutive_losses=risk.get("max_consecutive_losses", 3),
            cooldown_after_loss_sec=risk.get("cooldown_after_loss_sec", 180.0),
            cooldown_after_trade_sec=risk.get("cooldown_after_trade_sec", 120.0),
            min_payout_threshold=risk.get("min_payout_threshold", 0.70),
            session_end_hour_utc=risk.get("session_end_hour_utc", 21),
            enable_safe_mode=risk.get("enable_safe_mode", True),
            safe_mode_after_losses=risk.get("safe_mode_after_losses", 5),
        )

    def get_execution_config(self, profile_name: Optional[str] = None) -> ExecutionConfig:
        """Build ExecutionConfig from a profile."""
        name = profile_name or self.get_default_profile_name()
        profile = self.get_profile(name)
        exe = profile.get("execution", {})

        jitter = exe.get("jitter_range_ms", [50, 300])

        return ExecutionConfig(
            jitter_range_ms=(jitter[0], jitter[1]),
            min_spacing_sec=exe.get("min_spacing_sec", 5.0),
            max_retries=exe.get("max_retries", 2),
            max_latency_ms=exe.get("max_latency_ms", 500.0),
        )

    def get_trade_amount(self, profile_name: Optional[str] = None) -> float:
        """Get trade amount. Env var IQ_AMOUNT overrides profile config."""
        env_amount = os.environ.get("IQ_AMOUNT")
        if env_amount:
            try:
                return float(env_amount)
            except ValueError:
                pass
        name = profile_name or self.get_default_profile_name()
        profile = self.get_profile(name)
        return profile.get("trade", {}).get("amount", 5.0)

    def get_trade_duration(self, profile_name: Optional[str] = None) -> int:
        """Get trade duration (seconds). Env var IQ_DURATION overrides profile config."""
        env_duration = os.environ.get("IQ_DURATION")
        if env_duration:
            try:
                return int(env_duration)
            except ValueError:
                pass
        name = profile_name or self.get_default_profile_name()
        profile = self.get_profile(name)
        return profile.get("trade", {}).get("duration", 60)

    def get_scoring_min_score(self, profile_name: Optional[str] = None) -> float:
        """Get minimum scoring threshold for a profile."""
        name = profile_name or self.get_default_profile_name()
        profile = self.get_profile(name)
        return profile.get("scoring", {}).get("min_final_score", 0.55)

    def get_db_path(self) -> str:
        """Get database path."""
        return self.get_defaults().get("data", {}).get("db_path", "data/syntrix.db")

    def get_log_level(self) -> str:
        """Get logging level."""
        return self.get_defaults().get("logging", {}).get("level", "INFO")

    def get_trade_log_dir(self) -> str:
        """Get trade log directory."""
        return self.get_defaults().get("logging", {}).get("trade_log_dir", "data/trade_logs")
