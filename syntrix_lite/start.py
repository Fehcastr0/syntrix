"""
start.py — Syntrix Lite Launcher.

Launcher simples com Tkinter para iniciar o Syntrix Lite.
Modos: Shadow, Live Demo, Configurações.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk


ENV_FILE = ".env"
PROJECT_DIR = Path(__file__).parent


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


class SyntrixLiteLauncher:
    """Launcher window for Syntrix Lite."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Syntrix Lite — Launcher")
        self.root.geometry("520x700")
        self.root.resizable(False, False)
        self.root.configure(bg="#0a0a1a")
        self._process = None
        self._build_ui()
        self._load_config()

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"),
                        foreground="#00d4aa", background="#0a0a1a")
        style.configure("Sub.TLabel", font=("Segoe UI", 10),
                        foreground="#888888", background="#0a0a1a")
        style.configure("Info.TLabel", font=("Segoe UI", 10),
                        foreground="#aaaaaa", background="#0a0a1a")
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"),
                        foreground="#00d4aa", background="#0a0a1a")
        style.configure("TFrame", background="#0a0a1a")
        style.configure("Card.TFrame", background="#141428")

        style.configure("Shadow.TButton", font=("Segoe UI", 12, "bold"),
                        foreground="#ffffff", background="#2196f3", padding=12)
        style.map("Shadow.TButton",
                  background=[("active", "#1976d2"), ("disabled", "#333333")])

        style.configure("Live.TButton", font=("Segoe UI", 12, "bold"),
                        foreground="#ffffff", background="#27ae60", padding=12)
        style.map("Live.TButton",
                  background=[("active", "#219a52"), ("disabled", "#333333")])

        style.configure("Stop.TButton", font=("Segoe UI", 11, "bold"),
                        foreground="#ffffff", background="#e74c3c", padding=10)
        style.map("Stop.TButton",
                  background=[("active", "#c0392b"), ("disabled", "#333333")])

        style.configure("Config.TButton", font=("Segoe UI", 10),
                        foreground="#ffffff", background="#34495e", padding=8)
        style.map("Config.TButton",
                  background=[("active", "#2c3e50")])

        main = ttk.Frame(self.root, padding=20)
        main.pack(fill="both", expand=True)

        # Header
        ttk.Label(main, text="SYNTRIX LITE", style="Title.TLabel").pack(pady=(0, 2))
        ttk.Label(main, text="Validar edge real com simplicidade",
                  style="Sub.TLabel").pack(pady=(0, 10))

        # Config card
        config_card = ttk.Frame(main, style="Card.TFrame", padding=12)
        config_card.pack(fill="x", pady=(0, 10))

        # Email
        ttk.Label(config_card, text="Email:", style="Info.TLabel").pack(anchor="w")
        self.email_var = tk.StringVar()
        tk.Entry(config_card, textvariable=self.email_var, font=("Segoe UI", 10),
                 bg="#1a1a2e", fg="#ffffff", insertbackground="#ffffff",
                 relief="flat", bd=5).pack(fill="x", pady=(0, 5))

        # Password
        ttk.Label(config_card, text="Senha:", style="Info.TLabel").pack(anchor="w")
        self.password_var = tk.StringVar()
        tk.Entry(config_card, textvariable=self.password_var, show="*",
                 font=("Segoe UI", 10), bg="#1a1a2e", fg="#ffffff",
                 insertbackground="#ffffff", relief="flat", bd=5).pack(fill="x", pady=(0, 5))

        # Amount + Duration row
        row = ttk.Frame(config_card, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 5))

        left = ttk.Frame(row, style="Card.TFrame")
        left.pack(side="left", expand=True, fill="x", padx=(0, 5))
        ttk.Label(left, text="Valor ($):", style="Info.TLabel").pack(anchor="w")
        self.amount_var = tk.StringVar(value="2.0")
        tk.Entry(left, textvariable=self.amount_var, font=("Segoe UI", 10),
                 bg="#1a1a2e", fg="#ffffff", insertbackground="#ffffff",
                 relief="flat", bd=5, width=10).pack(fill="x")

        right = ttk.Frame(row, style="Card.TFrame")
        right.pack(side="right", expand=True, fill="x", padx=(5, 0))
        ttk.Label(right, text="Duração (s):", style="Info.TLabel").pack(anchor="w")
        self.duration_var = tk.StringVar(value="60")
        tk.Entry(right, textvariable=self.duration_var, font=("Segoe UI", 10),
                 bg="#1a1a2e", fg="#ffffff", insertbackground="#ffffff",
                 relief="flat", bd=5, width=10).pack(fill="x")

        # Interval
        row2 = ttk.Frame(config_card, style="Card.TFrame")
        row2.pack(fill="x", pady=(0, 5))
        ttk.Label(row2, text="Intervalo scan (s):", style="Info.TLabel").pack(anchor="w")
        self.interval_var = tk.StringVar(value="30")
        tk.Entry(row2, textvariable=self.interval_var, font=("Segoe UI", 10),
                 bg="#1a1a2e", fg="#ffffff", insertbackground="#ffffff",
                 relief="flat", bd=5, width=10).pack(anchor="w")

        # Save config button
        ttk.Button(config_card, text="Salvar Configuração",
                   style="Config.TButton", command=self._save_config).pack(fill="x", pady=(5, 0))

        # Status
        self.status_var = tk.StringVar(value="Pronto")
        ttk.Label(main, textvariable=self.status_var,
                  style="Status.TLabel").pack(pady=(5, 5))

        # Buttons
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(5, 5))

        ttk.Button(btn_frame, text="SHADOW MODE (Simular)",
                   style="Shadow.TButton",
                   command=self._start_shadow).pack(fill="x", pady=(0, 5))

        ttk.Button(btn_frame, text="LIVE MODE (Demo)",
                   style="Live.TButton",
                   command=self._start_live).pack(fill="x", pady=(0, 5))

        self.stop_btn = ttk.Button(btn_frame, text="PARAR",
                                   style="Stop.TButton",
                                   command=self._stop, state="disabled")
        self.stop_btn.pack(fill="x", pady=(0, 5))

        # Console
        ttk.Label(main, text="Console:", style="Info.TLabel").pack(anchor="w", pady=(5, 2))
        self.console = scrolledtext.ScrolledText(
            main, height=12, font=("Consolas", 9),
            bg="#0a0a1a", fg="#00d4aa", insertbackground="#00d4aa",
            relief="flat", bd=5, state="disabled",
        )
        self.console.pack(fill="both", expand=True)

    def _load_config(self) -> None:
        env = load_env()
        self.email_var.set(env.get("IQ_EMAIL", ""))
        self.password_var.set(env.get("IQ_PASSWORD", ""))
        self.amount_var.set(env.get("IQ_AMOUNT", "2.0"))
        self.duration_var.set(env.get("IQ_DURATION", "60"))
        self.interval_var.set(env.get("SCAN_INTERVAL", "30"))

    def _save_config(self) -> None:
        env = load_env()
        env["IQ_EMAIL"] = self.email_var.get().strip()
        env["IQ_PASSWORD"] = self.password_var.get().strip()
        env["IQ_AMOUNT"] = self.amount_var.get().strip()
        env["IQ_DURATION"] = self.duration_var.get().strip()
        env["SCAN_INTERVAL"] = self.interval_var.get().strip()
        if not env.get("IQ_PRACTICE"):
            env["IQ_PRACTICE"] = "true"
        if not env.get("STOP_GAIN"):
            env["STOP_GAIN"] = "30"
        if not env.get("STOP_LOSS"):
            env["STOP_LOSS"] = "-15"
        save_env(env)
        self.status_var.set("Configuração salva!")
        self._log("Configuração salva no .env")

    def _start_shadow(self) -> None:
        self._save_config()
        self._run_main(shadow=True)

    def _start_live(self) -> None:
        email = self.email_var.get().strip()
        password = self.password_var.get().strip()
        if not email or not password:
            messagebox.showerror("Erro", "Preencha email e senha!")
            return
        self._save_config()
        self._run_main(shadow=False)

    def _run_main(self, shadow: bool) -> None:
        if self._process:
            messagebox.showwarning("Aviso", "Já está rodando!")
            return

        interval = self.interval_var.get().strip() or "30"
        amount = self.amount_var.get().strip() or "2.0"
        duration = self.duration_var.get().strip() or "60"

        cmd = [
            sys.executable, str(PROJECT_DIR / "main.py"),
            "--interval", interval,
            "--amount", amount,
            "--duration", duration,
        ]
        if shadow:
            cmd.append("--shadow")

        mode = "SHADOW" if shadow else "LIVE"
        self.status_var.set(f"Rodando ({mode})...")
        self.stop_btn.configure(state="normal")
        self._log(f"Iniciando Syntrix Lite ({mode})...")

        def run():
            try:
                self._process = subprocess.Popen(
                    cmd, cwd=str(PROJECT_DIR),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
                for line in self._process.stdout:
                    self._log(line.rstrip())
                self._process.wait()
            except Exception as e:
                self._log(f"Erro: {e}")
            finally:
                self._process = None
                self.root.after(0, lambda: self.status_var.set("Parado"))
                self.root.after(0, lambda: self.stop_btn.configure(state="disabled"))

        threading.Thread(target=run, daemon=True).start()

    def _stop(self) -> None:
        if self._process:
            self._process.terminate()
            self._log("Parando Syntrix Lite...")
            self.status_var.set("Parando...")

    def _log(self, text: str) -> None:
        def _insert():
            self.console.configure(state="normal")
            self.console.insert("end", text + "\n")
            self.console.see("end")
            self.console.configure(state="disabled")
        self.root.after(0, _insert)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = SyntrixLiteLauncher()
    app.run()


if __name__ == "__main__":
    main()
