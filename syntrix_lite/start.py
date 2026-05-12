"""
start.py — Syntrix Lite Dashboard.

Painel visual intuitivo com:
- PnL em tempo real (grande, colorido)
- Wins/Losses/Winrate
- Último trade
- Log de atividade
- Configurações
- Motivo de parada
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk


ENV_FILE = ".env"
PROJECT_DIR = Path(__file__).parent
LOG_FILE = PROJECT_DIR / "syntrix_lite.log"


def ensure_dependencies() -> None:
    """Auto-install required dependencies if missing."""
    deps = [("pyyaml", "pyyaml"), ("fake_useragent", "fake_useragent")]
    for module, package in deps:
        try:
            __import__(module)
        except ImportError:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", package],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    try:
        __import__("iqbroker")
    except ImportError:
        try:
            __import__("iqoptionapi")
        except ImportError:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install",
                 "git+https://github.com/zagmi/iqbroker.git"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )


ensure_dependencies()


def load_env() -> dict:
    env = {}
    path = PROJECT_DIR / ENV_FILE
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def save_env(data: dict) -> None:
    path = PROJECT_DIR / ENV_FILE
    lines = []
    for k, v in data.items():
        lines.append(f"{k}={v}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── Colors ──────────────────────────────────────────
BG = "#0d1117"
BG_CARD = "#161b22"
BG_INPUT = "#0d1117"
GREEN = "#00d4aa"
RED = "#ff6b6b"
YELLOW = "#ffd93d"
WHITE = "#e6edf3"
GRAY = "#8b949e"
BLUE = "#58a6ff"
BORDER = "#30363d"


class SyntrixDashboard:
    """Intuitive dashboard for Syntrix Lite."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Syntrix Lite")
        self.root.geometry("600x820")
        self.root.minsize(550, 750)
        self.root.configure(bg=BG)
        self._process = None
        self._log_file = None

        # State
        self.pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.trades = 0
        self.winrate = 0.0
        self.last_trade = ""
        self.status = "PARADO"
        self.stop_reason = ""

        self._build_ui()
        self._load_config()

    # ── UI Builder ──────────────────────────────────
    def _build_ui(self) -> None:
        # Main container with scrollbar support
        main = tk.Frame(self.root, bg=BG, padx=15, pady=10)
        main.pack(fill="both", expand=True)

        # ── Header ──
        header = tk.Frame(main, bg=BG)
        header.pack(fill="x", pady=(0, 8))
        tk.Label(header, text="SYNTRIX LITE", font=("Segoe UI", 22, "bold"),
                 fg=GREEN, bg=BG).pack(side="left")
        self.status_label = tk.Label(header, text="PARADO", font=("Segoe UI", 12, "bold"),
                                     fg=GRAY, bg=BG)
        self.status_label.pack(side="right")

        # ── Dashboard Cards ──
        dash = tk.Frame(main, bg=BG)
        dash.pack(fill="x", pady=(0, 8))
        dash.columnconfigure(0, weight=1)
        dash.columnconfigure(1, weight=1)
        dash.columnconfigure(2, weight=1)

        # PnL Card (large)
        pnl_card = tk.Frame(dash, bg=BG_CARD, padx=12, pady=8,
                            highlightbackground=BORDER, highlightthickness=1)
        pnl_card.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        tk.Label(pnl_card, text="PnL", font=("Segoe UI", 10),
                 fg=GRAY, bg=BG_CARD).pack(anchor="w")
        self.pnl_label = tk.Label(pnl_card, text="$0.00",
                                   font=("Segoe UI", 36, "bold"),
                                   fg=WHITE, bg=BG_CARD)
        self.pnl_label.pack(anchor="w")

        # Win / Loss / Winrate cards
        self.win_label = self._stat_card(dash, "WINS", "0", GREEN, 1, 0)
        self.loss_label = self._stat_card(dash, "LOSSES", "0", RED, 1, 1)
        self.wr_label = self._stat_card(dash, "WINRATE", "0%", BLUE, 1, 2)

        # Trades + Last Trade
        info_row = tk.Frame(main, bg=BG)
        info_row.pack(fill="x", pady=(0, 8))
        info_row.columnconfigure(0, weight=1)
        info_row.columnconfigure(1, weight=2)

        trades_card = tk.Frame(info_row, bg=BG_CARD, padx=10, pady=6,
                                highlightbackground=BORDER, highlightthickness=1)
        trades_card.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        tk.Label(trades_card, text="TRADES", font=("Segoe UI", 9),
                 fg=GRAY, bg=BG_CARD).pack(anchor="w")
        self.trades_label = tk.Label(trades_card, text="0",
                                      font=("Segoe UI", 18, "bold"),
                                      fg=WHITE, bg=BG_CARD)
        self.trades_label.pack(anchor="w")

        last_card = tk.Frame(info_row, bg=BG_CARD, padx=10, pady=6,
                              highlightbackground=BORDER, highlightthickness=1)
        last_card.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        tk.Label(last_card, text="ULTIMO TRADE", font=("Segoe UI", 9),
                 fg=GRAY, bg=BG_CARD).pack(anchor="w")
        self.last_trade_label = tk.Label(last_card, text="Nenhum ainda",
                                          font=("Segoe UI", 11, "bold"),
                                          fg=GRAY, bg=BG_CARD)
        self.last_trade_label.pack(anchor="w")

        # ── Stop Reason Banner ──
        self.reason_frame = tk.Frame(main, bg=RED, padx=10, pady=6)
        self.reason_label = tk.Label(self.reason_frame, text="",
                                      font=("Segoe UI", 11, "bold"),
                                      fg=WHITE, bg=RED)
        self.reason_label.pack()
        # Hidden initially
        # self.reason_frame.pack(...)

        # ── Config Section (collapsible) ──
        config_header = tk.Frame(main, bg=BG)
        config_header.pack(fill="x", pady=(0, 4))
        self._config_visible = tk.BooleanVar(value=False)
        self.config_toggle = tk.Button(
            config_header, text="+ Configuracoes",
            font=("Segoe UI", 10, "bold"), fg=BLUE, bg=BG,
            bd=0, activebackground=BG, activeforeground=GREEN,
            cursor="hand2", command=self._toggle_config,
        )
        self.config_toggle.pack(anchor="w")

        self.config_frame = tk.Frame(main, bg=BG_CARD, padx=10, pady=8,
                                      highlightbackground=BORDER, highlightthickness=1)
        # Hidden initially

        self._build_config_fields()

        # ── Buttons ──
        btn_frame = tk.Frame(main, bg=BG)
        btn_frame.pack(fill="x", pady=(4, 6))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)
        btn_frame.columnconfigure(2, weight=1)

        self.shadow_btn = tk.Button(
            btn_frame, text="SHADOW", font=("Segoe UI", 11, "bold"),
            fg=WHITE, bg="#1f6feb", activebackground="#388bfd",
            bd=0, padx=15, pady=8, cursor="hand2",
            command=self._start_shadow,
        )
        self.shadow_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self.live_btn = tk.Button(
            btn_frame, text="LIVE", font=("Segoe UI", 11, "bold"),
            fg=WHITE, bg="#238636", activebackground="#2ea043",
            bd=0, padx=15, pady=8, cursor="hand2",
            command=self._start_live,
        )
        self.live_btn.grid(row=0, column=1, sticky="ew", padx=3)

        self.stop_btn = tk.Button(
            btn_frame, text="PARAR", font=("Segoe UI", 11, "bold"),
            fg=WHITE, bg="#da3633", activebackground="#f85149",
            bd=0, padx=15, pady=8, cursor="hand2",
            command=self._stop, state="disabled",
        )
        self.stop_btn.grid(row=0, column=2, sticky="ew", padx=(3, 0))

        # ── Log Console ──
        log_header = tk.Frame(main, bg=BG)
        log_header.pack(fill="x", pady=(0, 2))
        tk.Label(log_header, text="LOG", font=("Segoe UI", 10, "bold"),
                 fg=GRAY, bg=BG).pack(side="left")
        tk.Button(log_header, text="Limpar", font=("Segoe UI", 8),
                  fg=GRAY, bg=BG, bd=0, activebackground=BG,
                  cursor="hand2", command=self._clear_log).pack(side="right")

        self.console = tk.Text(
            main, height=12, font=("Consolas", 9),
            bg=BG_CARD, fg=GREEN, insertbackground=GREEN,
            relief="flat", bd=0, wrap="word",
            highlightbackground=BORDER, highlightthickness=1,
        )
        self.console.pack(fill="both", expand=True)
        self.console.configure(state="disabled")

        # Tags for colored text
        self.console.tag_configure("win", foreground=GREEN)
        self.console.tag_configure("loss", foreground=RED)
        self.console.tag_configure("info", foreground=BLUE)
        self.console.tag_configure("warn", foreground=YELLOW)
        self.console.tag_configure("error", foreground=RED)
        self.console.tag_configure("normal", foreground=GREEN)

        # ── Footer ──
        footer = tk.Frame(main, bg=BG)
        footer.pack(fill="x", pady=(4, 0))
        tk.Label(footer, text="Log salvo em: syntrix_lite.log",
                 font=("Segoe UI", 8), fg=GRAY, bg=BG).pack(side="left")
        tk.Label(footer, text="v1.3.1",
                 font=("Segoe UI", 8), fg=GRAY, bg=BG).pack(side="right")

    def _stat_card(self, parent, title, value, color, row, col):
        card = tk.Frame(parent, bg=BG_CARD, padx=10, pady=6,
                        highlightbackground=BORDER, highlightthickness=1)
        card.grid(row=row, column=col, sticky="ew", padx=2, pady=0)
        tk.Label(card, text=title, font=("Segoe UI", 9),
                 fg=GRAY, bg=BG_CARD).pack(anchor="w")
        label = tk.Label(card, text=value, font=("Segoe UI", 20, "bold"),
                         fg=color, bg=BG_CARD)
        label.pack(anchor="w")
        return label

    def _build_config_fields(self) -> None:
        f = self.config_frame

        # Email
        tk.Label(f, text="Email:", font=("Segoe UI", 9), fg=GRAY, bg=BG_CARD).pack(anchor="w")
        self.email_var = tk.StringVar()
        tk.Entry(f, textvariable=self.email_var, font=("Segoe UI", 10),
                 bg=BG_INPUT, fg=WHITE, insertbackground=WHITE,
                 relief="flat", bd=4).pack(fill="x", pady=(0, 4))

        # Password
        tk.Label(f, text="Senha:", font=("Segoe UI", 9), fg=GRAY, bg=BG_CARD).pack(anchor="w")
        self.password_var = tk.StringVar()
        tk.Entry(f, textvariable=self.password_var, show="*",
                 font=("Segoe UI", 10), bg=BG_INPUT, fg=WHITE,
                 insertbackground=WHITE, relief="flat", bd=4).pack(fill="x", pady=(0, 4))

        # Row: Amount + Duration
        row1 = tk.Frame(f, bg=BG_CARD)
        row1.pack(fill="x", pady=(0, 4))
        row1.columnconfigure(0, weight=1)
        row1.columnconfigure(1, weight=1)

        lf = tk.Frame(row1, bg=BG_CARD)
        lf.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        tk.Label(lf, text="Valor ($):", font=("Segoe UI", 9), fg=GRAY, bg=BG_CARD).pack(anchor="w")
        self.amount_var = tk.StringVar(value="2.0")
        tk.Entry(lf, textvariable=self.amount_var, font=("Segoe UI", 10),
                 bg=BG_INPUT, fg=WHITE, insertbackground=WHITE,
                 relief="flat", bd=4, width=8).pack(fill="x")

        rf = tk.Frame(row1, bg=BG_CARD)
        rf.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        tk.Label(rf, text="Duracao (min):", font=("Segoe UI", 9), fg=GRAY, bg=BG_CARD).pack(anchor="w")
        self.duration_var = tk.StringVar(value="1")
        tk.Entry(rf, textvariable=self.duration_var, font=("Segoe UI", 10),
                 bg=BG_INPUT, fg=WHITE, insertbackground=WHITE,
                 relief="flat", bd=4, width=8).pack(fill="x")

        # Row: Interval + Target
        row2 = tk.Frame(f, bg=BG_CARD)
        row2.pack(fill="x", pady=(0, 4))
        row2.columnconfigure(0, weight=1)
        row2.columnconfigure(1, weight=1)

        lf2 = tk.Frame(row2, bg=BG_CARD)
        lf2.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        tk.Label(lf2, text="Intervalo (s):", font=("Segoe UI", 9), fg=GRAY, bg=BG_CARD).pack(anchor="w")
        self.interval_var = tk.StringVar(value="30")
        tk.Entry(lf2, textvariable=self.interval_var, font=("Segoe UI", 10),
                 bg=BG_INPUT, fg=WHITE, insertbackground=WHITE,
                 relief="flat", bd=4, width=8).pack(fill="x")

        rf2 = tk.Frame(row2, bg=BG_CARD)
        rf2.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        tk.Label(rf2, text="Meta Lucro ($):", font=("Segoe UI", 9), fg=GRAY, bg=BG_CARD).pack(anchor="w")
        self.target_var = tk.StringVar(value="0")
        tk.Entry(rf2, textvariable=self.target_var, font=("Segoe UI", 10),
                 bg=BG_INPUT, fg=GREEN, insertbackground=GREEN,
                 relief="flat", bd=4, width=8).pack(fill="x")

        # Row: Stop Gain + Stop Loss
        row3 = tk.Frame(f, bg=BG_CARD)
        row3.pack(fill="x", pady=(0, 4))
        row3.columnconfigure(0, weight=1)
        row3.columnconfigure(1, weight=1)

        lf3 = tk.Frame(row3, bg=BG_CARD)
        lf3.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        tk.Label(lf3, text="Stop Gain ($):", font=("Segoe UI", 9), fg=GRAY, bg=BG_CARD).pack(anchor="w")
        self.stop_gain_var = tk.StringVar(value="30")
        tk.Entry(lf3, textvariable=self.stop_gain_var, font=("Segoe UI", 10),
                 bg=BG_INPUT, fg=GREEN, insertbackground=GREEN,
                 relief="flat", bd=4, width=8).pack(fill="x")

        rf3 = tk.Frame(row3, bg=BG_CARD)
        rf3.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        tk.Label(rf3, text="Stop Loss ($):", font=("Segoe UI", 9), fg=GRAY, bg=BG_CARD).pack(anchor="w")
        self.stop_loss_var = tk.StringVar(value="-15")
        tk.Entry(rf3, textvariable=self.stop_loss_var, font=("Segoe UI", 10),
                 bg=BG_INPUT, fg=RED, insertbackground=RED,
                 relief="flat", bd=4, width=8).pack(fill="x")

        # Force entry
        self.force_var = tk.BooleanVar(value=False)
        tk.Checkbutton(f, text="FORCE ENTRY (debug)",
                       variable=self.force_var, font=("Segoe UI", 9),
                       bg=BG_CARD, fg=RED, selectcolor=BG_INPUT,
                       activebackground=BG_CARD, activeforeground=RED,
                       ).pack(anchor="w", pady=(2, 4))

        # Save button
        tk.Button(f, text="Salvar Configuracao", font=("Segoe UI", 10, "bold"),
                  fg=WHITE, bg="#30363d", activebackground="#484f58",
                  bd=0, padx=10, pady=5, cursor="hand2",
                  command=self._save_config).pack(fill="x")

    def _toggle_config(self) -> None:
        if self._config_visible.get():
            self.config_frame.pack_forget()
            self.config_toggle.configure(text="+ Configuracoes")
            self._config_visible.set(False)
        else:
            self.config_frame.pack(fill="x", pady=(0, 6),
                                   after=self.config_toggle.master)
            self.config_toggle.configure(text="- Configuracoes")
            self._config_visible.set(True)

    # ── Config Load/Save ──────────────────────────
    def _load_config(self) -> None:
        env = load_env()
        self.email_var.set(env.get("IQ_EMAIL", ""))
        self.password_var.set(env.get("IQ_PASSWORD", ""))
        self.amount_var.set(env.get("IQ_AMOUNT", "2.0"))
        self.duration_var.set(env.get("IQ_DURATION", "1"))
        self.interval_var.set(env.get("SCAN_INTERVAL", "30"))
        self.target_var.set(env.get("PROFIT_TARGET", "0"))
        self.stop_gain_var.set(env.get("STOP_GAIN", "30"))
        self.stop_loss_var.set(env.get("STOP_LOSS", "-15"))

    def _save_config(self) -> None:
        env = load_env()
        env["IQ_EMAIL"] = self.email_var.get().strip()
        env["IQ_PASSWORD"] = self.password_var.get().strip()
        env["IQ_AMOUNT"] = self.amount_var.get().strip() or "2.0"
        env["IQ_DURATION"] = self.duration_var.get().strip() or "1"
        env["STOP_GAIN"] = self.stop_gain_var.get().strip() or "30"
        env["STOP_LOSS"] = self.stop_loss_var.get().strip() or "-15"
        if not env.get("IQ_PRACTICE"):
            env["IQ_PRACTICE"] = "true"

        target_val = self.target_var.get().strip() or "0"
        if float(target_val) > 0:
            env["PROFIT_TARGET"] = target_val
        elif "PROFIT_TARGET" in env:
            del env["PROFIT_TARGET"]

        save_env(env)
        self._log("Configuracao salva!", "info")

    # ── Bot Control ──────────────────────────────
    def _start_shadow(self) -> None:
        self._save_config()
        self._run_bot(shadow=True)

    def _start_live(self) -> None:
        email = self.email_var.get().strip()
        password = self.password_var.get().strip()
        if not email or not password:
            messagebox.showerror("Erro", "Preencha email e senha nas configuracoes!")
            return
        self._save_config()
        self._run_bot(shadow=False)

    def _run_bot(self, shadow: bool) -> None:
        if self._process:
            messagebox.showwarning("Aviso", "Ja esta rodando!")
            return

        # Reset dashboard
        self.pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.trades = 0
        self.winrate = 0.0
        self.stop_reason = ""
        self._update_dashboard()
        self.reason_frame.pack_forget()

        interval = self.interval_var.get().strip() or "30"
        amount = self.amount_var.get().strip() or "2.0"
        duration = self.duration_var.get().strip() or "1"
        target = self.target_var.get().strip() or "0"

        cmd = [
            sys.executable, "-u",  # -u = unbuffered output!
            str(PROJECT_DIR / "main.py"),
            "--interval", interval,
            "--amount", amount,
            "--duration", duration,
        ]
        if shadow:
            cmd.append("--shadow")
        if self.force_var.get():
            cmd.append("--force")
        if float(target) > 0:
            cmd.extend(["--target", target])

        mode = "SHADOW" if shadow else "LIVE"
        self._set_status(f"RODANDO ({mode})", GREEN)
        self.stop_btn.configure(state="normal")
        self.shadow_btn.configure(state="disabled")
        self.live_btn.configure(state="disabled")
        self._log(f"Iniciando Syntrix Lite ({mode})...", "info")

        # Open log file
        self._log_file = open(LOG_FILE, "a", encoding="utf-8")
        self._log_file.write(f"\n{'='*60}\n")
        self._log_file.write(f"  Sessao iniciada: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        self._log_file.write(f"  Modo: {mode}\n")
        self._log_file.write(f"{'='*60}\n")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        def run():
            try:
                self._process = subprocess.Popen(
                    cmd, cwd=str(PROJECT_DIR),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, env=env,
                )
                for line in self._process.stdout:
                    line = line.rstrip()
                    if line:
                        self.root.after(0, self._process_line, line)
                        if self._log_file:
                            self._log_file.write(line + "\n")
                            self._log_file.flush()
                self._process.wait()
            except Exception as e:
                self.root.after(0, self._log, f"Erro: {e}", "error")
            finally:
                self._process = None
                if self._log_file:
                    self._log_file.close()
                    self._log_file = None
                self.root.after(0, self._on_bot_stopped)

        threading.Thread(target=run, daemon=True).start()

    def _process_line(self, line: str) -> None:
        """Parse log line and update dashboard."""
        # Determine tag
        tag = "normal"
        if "[ERROR]" in line:
            tag = "error"
        elif "[WARNING]" in line or "[WARN]" in line:
            tag = "warn"
        elif "WIN" in line or "win" in line or "SUCCESS" in line:
            tag = "win"
        elif "LOSS" in line or "FAIL" in line or "BLOCK" in line:
            tag = "loss"
        elif "[INFO]" in line:
            tag = "info"

        self._log(line, tag)

        # Parse [STATS] line for dashboard update
        stats_match = re.search(
            r'\[STATS\]\s+trades=(\d+)\s+WR=([\d.]+)%\s+PnL=\$([-\d.]+)\s+W=(\d+)\s+L=(\d+)',
            line
        )
        if stats_match:
            self.trades = int(stats_match.group(1))
            self.winrate = float(stats_match.group(2))
            self.pnl = float(stats_match.group(3))
            self.wins = int(stats_match.group(4))
            self.losses = int(stats_match.group(5))
            self._update_dashboard()

        # Parse [RESULT] line for last trade
        result_match = re.search(
            r'\[RESULT\]\s+(\w+)\s+(\S+)\s+->\s+(\w+)\s+\(\$([-\d.]+)\)',
            line
        )
        if result_match:
            direction = result_match.group(1)
            asset = result_match.group(2)
            result = result_match.group(3)
            profit = result_match.group(4)
            self.last_trade = f"{direction} {asset} = {result} (${profit})"
            self.last_trade_label.configure(
                text=self.last_trade,
                fg=GREEN if result.lower() == "win" else RED,
            )

        # Parse session end
        if "SESSAO ENCERRADA" in line:
            pass  # Will be handled by _on_bot_stopped
        if "MOTIVO:" in line:
            reason = line.split("MOTIVO:")[-1].strip()
            self.stop_reason = reason

        # Parse [EXECUTION] SUCCESS
        if "[EXECUTION] SUCCESS" in line:
            exec_match = re.search(r'SUCCESS:\s+(\S+)', line)
            if exec_match:
                asset = exec_match.group(1)
                self.last_trade_label.configure(text=f"Executando {asset}...", fg=YELLOW)

        # Parse PnL from result line
        pnl_match = re.search(r'PnL:\s+\$([-\d.]+)', line)
        if pnl_match and "[RESULT]" in line:
            self.pnl = float(pnl_match.group(1))
            self._update_pnl()

    def _update_dashboard(self) -> None:
        """Update all dashboard values."""
        self._update_pnl()
        self.win_label.configure(text=str(self.wins))
        self.loss_label.configure(text=str(self.losses))
        self.wr_label.configure(text=f"{self.winrate:.0f}%")
        self.trades_label.configure(text=str(self.trades))

    def _update_pnl(self) -> None:
        """Update PnL display with color."""
        pnl_str = f"${self.pnl:+.2f}" if self.pnl != 0 else "$0.00"
        color = GREEN if self.pnl > 0 else (RED if self.pnl < 0 else WHITE)
        self.pnl_label.configure(text=pnl_str, fg=color)

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.configure(text=text, fg=color)

    def _on_bot_stopped(self) -> None:
        """Called when bot process ends."""
        self._set_status("PARADO", GRAY)
        self.stop_btn.configure(state="disabled")
        self.shadow_btn.configure(state="normal")
        self.live_btn.configure(state="normal")

        if self.stop_reason:
            self.reason_label.configure(text=f"PAROU: {self.stop_reason}")
            # Color based on reason
            if "LUCRO" in self.stop_reason or "GAIN" in self.stop_reason:
                self.reason_frame.configure(bg="#238636")
                self.reason_label.configure(bg="#238636")
            else:
                self.reason_frame.configure(bg="#da3633")
                self.reason_label.configure(bg="#da3633")
            self.reason_frame.pack(fill="x", pady=(0, 6))
        else:
            self._log("Bot parou.", "warn")

    def _stop(self) -> None:
        if self._process:
            self._process.terminate()
            self._log("Parando Syntrix Lite...", "warn")
            self._set_status("PARANDO...", YELLOW)

    def _log(self, text: str, tag: str = "normal") -> None:
        self.console.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        # Don't add timestamp if line already has one
        if re.match(r'\d{2}:\d{2}:\d{2}', text):
            self.console.insert("end", text + "\n", tag)
        else:
            self.console.insert("end", f"{ts}  {text}\n", tag)
        self.console.see("end")
        self.console.configure(state="disabled")

    def _clear_log(self) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = SyntrixDashboard()
    app.run()


if __name__ == "__main__":
    main()
