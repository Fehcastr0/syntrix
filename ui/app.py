"""
ui/app.py — Lightweight Tkinter UI for Syntrix.

Rules:
- NEVER processes logic
- NEVER calculates analytics
- NEVER blocks core
- Max update rate: 2 seconds
- Shows only essential status info
- NO real-time charts, NO animations, NO heavy dashboards
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("syntrix.ui")

UPDATE_INTERVAL_MS = 2000


class SyntrixUI:
    """
    Lightweight Tkinter dashboard for Syntrix.

    Displays:
    - Current state
    - Profile & mode
    - PnL & drawdown
    - Trade count & winrate
    - Last trade info
    - Last 50 log lines

    All data is pushed via update_data() — UI never pulls.
    """

    def __init__(self) -> None:
        self._root: Optional[tk.Tk] = None
        self._running = False
        self._data_queue: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=100)
        self._log_queue: queue.Queue[str] = queue.Queue(maxsize=200)
        self._thread: Optional[threading.Thread] = None
        self._on_stop: Optional[Callable[[], None]] = None

        # Widget references
        self._state_var: Optional[tk.StringVar] = None
        self._profile_var: Optional[tk.StringVar] = None
        self._mode_var: Optional[tk.StringVar] = None
        self._pnl_var: Optional[tk.StringVar] = None
        self._trades_var: Optional[tk.StringVar] = None
        self._drawdown_var: Optional[tk.StringVar] = None
        self._last_trade_var: Optional[tk.StringVar] = None
        self._winrate_var: Optional[tk.StringVar] = None
        self._log_text: Optional[scrolledtext.ScrolledText] = None

    def start(self, on_stop: Optional[Callable[[], None]] = None) -> None:
        """Start the UI in a separate thread."""
        if self._running:
            return
        self._on_stop = on_stop
        self._running = True
        self._thread = threading.Thread(target=self._run, name="syntrix-ui", daemon=True)
        self._thread.start()
        logger.info("UI started")

    def stop(self) -> None:
        """Stop the UI."""
        self._running = False
        if self._root:
            try:
                self._root.quit()
            except Exception:
                pass
        logger.info("UI stopped")

    def update_data(self, data: Dict[str, Any]) -> None:
        """Push status data to UI (non-blocking, fire-and-forget)."""
        try:
            self._data_queue.put_nowait(data)
        except queue.Full:
            pass  # drop update silently

    def push_log(self, line: str) -> None:
        """Push a log line to UI display (non-blocking)."""
        try:
            self._log_queue.put_nowait(line)
        except queue.Full:
            pass

    def _run(self) -> None:
        """Main UI thread."""
        try:
            self._root = tk.Tk()
            self._root.title("Syntrix — Quantitative Operating System")
            self._root.geometry("700x600")
            self._root.resizable(True, True)
            self._root.configure(bg="#1a1a2e")
            self._root.protocol("WM_DELETE_WINDOW", self._on_close)

            self._build_ui()
            self._schedule_update()
            self._root.mainloop()
        except Exception as exc:
            logger.error("UI error: %s", exc, exc_info=True)
        finally:
            self._running = False

    def _build_ui(self) -> None:
        """Build all UI widgets."""
        assert self._root is not None

        style = ttk.Style()
        style.theme_use("clam")

        main_frame = ttk.Frame(self._root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ── Header ──
        header = ttk.Label(
            main_frame,
            text="SYNTRIX",
            font=("Consolas", 18, "bold"),
        )
        header.pack(pady=(0, 10))

        # ── Status Grid ──
        status_frame = ttk.LabelFrame(main_frame, text="Status", padding=8)
        status_frame.pack(fill=tk.X, pady=5)

        self._state_var = tk.StringVar(value="IDLE")
        self._profile_var = tk.StringVar(value="—")
        self._mode_var = tk.StringVar(value="—")
        self._pnl_var = tk.StringVar(value="$0.00")
        self._trades_var = tk.StringVar(value="0")
        self._drawdown_var = tk.StringVar(value="0.0%")
        self._winrate_var = tk.StringVar(value="0.0%")
        self._last_trade_var = tk.StringVar(value="—")

        labels = [
            ("Estado:", self._state_var),
            ("Perfil:", self._profile_var),
            ("Modo:", self._mode_var),
            ("PnL:", self._pnl_var),
            ("Trades:", self._trades_var),
            ("Drawdown:", self._drawdown_var),
            ("Winrate:", self._winrate_var),
            ("Último Trade:", self._last_trade_var),
        ]

        for i, (label, var) in enumerate(labels):
            row = i // 2
            col = (i % 2) * 2
            ttk.Label(status_frame, text=label, font=("Consolas", 10, "bold")).grid(
                row=row, column=col, sticky=tk.W, padx=5, pady=2
            )
            ttk.Label(status_frame, textvariable=var, font=("Consolas", 10)).grid(
                row=row, column=col + 1, sticky=tk.W, padx=5, pady=2
            )

        # ── Log Display ──
        log_frame = ttk.LabelFrame(main_frame, text="Log (últimas 50 linhas)", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self._log_text = scrolledtext.ScrolledText(
            log_frame,
            height=15,
            font=("Consolas", 8),
            bg="#0d1117",
            fg="#c9d1d9",
            insertbackground="#c9d1d9",
            state=tk.DISABLED,
            wrap=tk.WORD,
        )
        self._log_text.pack(fill=tk.BOTH, expand=True)

        # ── Footer ──
        footer_frame = ttk.Frame(main_frame)
        footer_frame.pack(fill=tk.X, pady=5)

        stop_btn = ttk.Button(footer_frame, text="Parar Sistema", command=self._on_close)
        stop_btn.pack(side=tk.RIGHT)

    def _schedule_update(self) -> None:
        """Schedule periodic UI updates."""
        if not self._running or not self._root:
            return
        self._process_data()
        self._process_logs()
        self._root.after(UPDATE_INTERVAL_MS, self._schedule_update)

    def _process_data(self) -> None:
        """Process queued status updates."""
        latest: Optional[Dict[str, Any]] = None
        while not self._data_queue.empty():
            try:
                latest = self._data_queue.get_nowait()
            except queue.Empty:
                break

        if latest and self._state_var:
            self._state_var.set(latest.get("state", "—"))
            self._profile_var.set(latest.get("profile", "—"))
            self._mode_var.set(latest.get("mode", "—"))
            self._pnl_var.set(f"${latest.get('pnl', 0):.2f}")
            self._trades_var.set(str(latest.get("total_trades", 0)))
            self._drawdown_var.set(f"{latest.get('drawdown_pct', 0):.1f}%")
            self._winrate_var.set(f"{latest.get('winrate', 0):.1f}%")
            last = latest.get("last_trade", "—")
            self._last_trade_var.set(str(last))

    def _process_logs(self) -> None:
        """Process queued log lines."""
        if not self._log_text:
            return

        lines: List[str] = []
        while not self._log_queue.empty():
            try:
                lines.append(self._log_queue.get_nowait())
            except queue.Empty:
                break

        if lines:
            self._log_text.configure(state=tk.NORMAL)
            for line in lines:
                self._log_text.insert(tk.END, line + "\n")

            # Keep only last 50 lines
            content = self._log_text.get("1.0", tk.END)
            all_lines = content.strip().split("\n")
            if len(all_lines) > 50:
                self._log_text.delete("1.0", tk.END)
                self._log_text.insert("1.0", "\n".join(all_lines[-50:]) + "\n")

            self._log_text.see(tk.END)
            self._log_text.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        """Handle window close."""
        self._running = False
        if self._on_stop:
            self._on_stop()
        if self._root:
            self._root.destroy()


class UILogHandler(logging.Handler):
    """Custom log handler that pushes lines to the UI."""

    def __init__(self, ui: SyntrixUI) -> None:
        super().__init__()
        self._ui = ui

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._ui.push_log(msg)
        except Exception:
            pass
