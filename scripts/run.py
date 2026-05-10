#!/usr/bin/env python3
"""Quick-start script for Syntrix."""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import Syntrix


def run_dry():
    """Run in dry-run mode (no broker connection)."""
    syntrix = Syntrix()
    syntrix.start(headless=True)
    print("\nSyntrix running in dry-run mode.")
    print("Press Ctrl+C to stop.\n")
    try:
        syntrix.run(interval=30.0)
    except KeyboardInterrupt:
        syntrix.stop()


def run_demo():
    """Run in demo mode with IQ Option practice account."""
    syntrix = Syntrix()
    syntrix._mode = "demo"
    syntrix.start(headless=False)
    syntrix.run(interval=60.0)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "dry"
    if mode == "demo":
        run_demo()
    else:
        run_dry()
