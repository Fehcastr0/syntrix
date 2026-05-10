"""
iq_api.py — IQ Option API loader for Syntrix.

Handles importing the correct IQ Option package (iqbroker or iqoptionapi)
and auto-installs missing dependencies if needed.
"""

from __future__ import annotations

import subprocess
import sys


def _install_package(package: str) -> bool:
    """Install a pip package."""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _install_iqbroker() -> bool:
    """Install iqbroker and its dependencies."""
    _install_package("fake_useragent")
    return _install_package("git+https://github.com/zagmi/iqbroker.git")


def get_iq_option_class():
    """
    Get the IQ_Option class from available packages.

    Tries iqbroker first, then iqoptionapi.
    Auto-installs iqbroker if neither is available.

    Returns:
        (IQ_Option class, None) on success
        (None, error_message) on failure
    """
    # Try iqbroker first
    try:
        from iqbroker.stable_api import IQ_Option
        return IQ_Option, None
    except ImportError:
        pass
    except Exception:
        # iqbroker exists but has missing dependencies
        try:
            _install_package("fake_useragent")
            from iqbroker.stable_api import IQ_Option
            return IQ_Option, None
        except Exception:
            pass

    # Try iqoptionapi
    try:
        from iqoptionapi.stable_api import IQ_Option
        return IQ_Option, None
    except Exception:
        pass

    # Nothing works — try auto-installing iqbroker
    if _install_iqbroker():
        try:
            from iqbroker.stable_api import IQ_Option
            return IQ_Option, None
        except Exception as e:
            return None, f"Instalou iqbroker mas falhou ao importar: {e}"

    return None, (
        "IQ Option API nao encontrada.\n\n"
        "Rode o instalar.bat ou execute:\n"
        "pip install fake_useragent\n"
        "pip install git+https://github.com/zagmi/iqbroker.git"
    )
