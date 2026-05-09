"""
start.py — Syntrix Launcher.

Main entry point for the Syntrix system. Provides a menu to:
- Login to IQ Option (opens login.py)
- Start Syntrix in demo or dry-run mode
- View analytics report
- Run tests
- Open configuration
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


def load_env() -> dict:
    """Load .env values."""
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


def has_credentials() -> bool:
    """Check if credentials are saved."""
    env = load_env()
    return bool(env.get("IQ_EMAIL")) and bool(env.get("IQ_PASSWORD"))


class SyntrixLauncher:
    """Main launcher window for Syntrix."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Syntrix — Launcher")
        self.root.geometry("500x600")
        self.root.resizable(False, False)
        self.root.configure(bg="#0f0f23")
        self._process = None

        self._build_ui()
        self._update_status()

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        # Styles
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"),
                        foreground="#e94560", background="#0f0f23")
        style.configure("Sub.TLabel", font=("Segoe UI", 9),
                        foreground="#666666", background="#0f0f23")
        style.configure("Info.TLabel", font=("Segoe UI", 10),
                        foreground="#aaaaaa", background="#0f0f23")
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"),
                        foreground="#00d2d3", background="#0f0f23")
        style.configure("TFrame", background="#0f0f23")
        style.configure("Card.TFrame", background="#16213e")

        style.configure("Login.TButton", font=("Segoe UI", 11),
                        foreground="#ffffff", background="#0f3460", padding=10)
        style.map("Login.TButton",
                  background=[("active", "#16213e")])

        style.configure("Start.TButton", font=("Segoe UI", 12, "bold"),
                        foreground="#ffffff", background="#27ae60", padding=12)
        style.map("Start.TButton",
                  background=[("active", "#219a52"), ("disabled", "#333333")])

        style.configure("DryRun.TButton", font=("Segoe UI", 11),
                        foreground="#ffffff", background="#f39c12", padding=10)
        style.map("DryRun.TButton",
                  background=[("active", "#e67e22")])

        style.configure("Tool.TButton", font=("Segoe UI", 10),
                        foreground="#ffffff", background="#2c3e50", padding=8)
        style.map("Tool.TButton",
                  background=[("active", "#34495e")])

        style.configure("Stop.TButton", font=("Segoe UI", 10, "bold"),
                        foreground="#ffffff", background="#c0392b", padding=8)
        style.map("Stop.TButton",
                  background=[("active", "#e74c3c"), ("disabled", "#333333")])

        main = ttk.Frame(self.root, padding=25)
        main.pack(fill="both", expand=True)

        # Header
        ttk.Label(main, text="SYNTRIX", style="Title.TLabel").pack(pady=(0, 2))
        ttk.Label(main, text="Quantitative Operating System",
                  style="Sub.TLabel").pack(pady=(0, 5))

        # Status card
        status_card = ttk.Frame(main, style="Card.TFrame", padding=12)
        status_card.pack(fill="x", pady=(10, 15))

        self.status_var = tk.StringVar(value="Verificando...")
        ttk.Label(status_card, textvariable=self.status_var,
                  font=("Segoe UI", 10), foreground="#aaaaaa",
                  background="#16213e").pack(anchor="w")

        self.account_var = tk.StringVar(value="")
        ttk.Label(status_card, textvariable=self.account_var,
                  font=("Segoe UI", 10, "bold"), foreground="#00d2d3",
                  background="#16213e").pack(anchor="w")

        self.mode_var = tk.StringVar(value="")
        ttk.Label(status_card, textvariable=self.mode_var,
                  font=("Segoe UI", 10), foreground="#e94560",
                  background="#16213e").pack(anchor="w")

        # Buttons section
        btn_section = ttk.Frame(main)
        btn_section.pack(fill="x", pady=(5, 0))

        # Login button
        self.login_btn = ttk.Button(btn_section, text="Login IQ Option",
                                     style="Login.TButton", command=self._on_login)
        self.login_btn.pack(fill="x", pady=(0, 8))

        # Start buttons row
        start_row = ttk.Frame(btn_section)
        start_row.pack(fill="x", pady=(0, 8))

        self.start_btn = ttk.Button(start_row, text="Iniciar Syntrix",
                                     style="Start.TButton", command=self._on_start)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.dryrun_btn = ttk.Button(start_row, text="Dry-Run",
                                      style="DryRun.TButton", command=self._on_dryrun)
        self.dryrun_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        # Stop button
        self.stop_btn = ttk.Button(btn_section, text="Parar Syntrix",
                                    style="Stop.TButton", command=self._on_stop)
        self.stop_btn.pack(fill="x", pady=(0, 8))
        self.stop_btn.configure(state="disabled")

        # Tools row
        tools_row = ttk.Frame(btn_section)
        tools_row.pack(fill="x", pady=(0, 8))

        ttk.Button(tools_row, text="Analytics",
                   style="Tool.TButton", command=self._on_analytics).pack(
                       side="left", expand=True, fill="x", padx=(0, 3))

        ttk.Button(tools_row, text="Testes",
                   style="Tool.TButton", command=self._on_tests).pack(
                       side="left", expand=True, fill="x", padx=(3, 3))

        ttk.Button(tools_row, text="Config",
                   style="Tool.TButton", command=self._on_config).pack(
                       side="left", expand=True, fill="x", padx=(3, 0))

        # Output console
        ttk.Label(main, text="Console:", style="Info.TLabel").pack(anchor="w", pady=(10, 3))
        self.console = scrolledtext.ScrolledText(
            main, height=10, bg="#0a0a1a", fg="#00ff00",
            font=("Consolas", 9), insertbackground="#00ff00",
            state="disabled", wrap="word"
        )
        self.console.pack(fill="both", expand=True)

    def _log(self, text: str) -> None:
        """Write text to the console."""
        self.console.configure(state="normal")
        self.console.insert("end", text + "\n")
        self.console.see("end")
        self.console.configure(state="disabled")

    def _update_status(self) -> None:
        """Update status display."""
        env = load_env()
        email = env.get("IQ_EMAIL", "")
        practice = env.get("IQ_PRACTICE", "true")
        mode_text = "Demo" if practice.lower() == "true" else "Real"

        if email:
            self.status_var.set("Conta configurada:")
            self.account_var.set(f"  {email}")
            self.mode_var.set(f"  Modo: {mode_text}")
            self.login_btn.configure(text=f"Login IQ Option ({email})")
        else:
            self.status_var.set("Nenhuma conta configurada")
            self.account_var.set("  Faca login para conectar")
            self.mode_var.set("")
            self.login_btn.configure(text="Login IQ Option")

    def _on_login(self) -> None:
        """Open login app."""
        self._log("[LAUNCHER] Abrindo login...")
        subprocess.Popen(
            [sys.executable, str(PROJECT_DIR / "login.py")],
            cwd=str(PROJECT_DIR),
        )
        self.root.after(3000, self._update_status)

    def _on_start(self) -> None:
        """Start Syntrix with broker connection."""
        if not has_credentials():
            messagebox.showwarning("Syntrix", "Faca login primeiro!")
            return

        self._log("[LAUNCHER] Iniciando Syntrix...")
        self._run_syntrix([sys.executable, str(PROJECT_DIR / "main.py")])

    def _on_dryrun(self) -> None:
        """Start Syntrix in dry-run mode."""
        self._log("[LAUNCHER] Iniciando Syntrix em dry-run...")
        self._run_syntrix([sys.executable, str(PROJECT_DIR / "main.py"), "--headless"])

    def _run_syntrix(self, cmd: list) -> None:
        """Run Syntrix subprocess and stream output."""
        if self._process and self._process.poll() is None:
            messagebox.showwarning("Syntrix", "Syntrix ja esta rodando!")
            return

        self.start_btn.configure(state="disabled")
        self.dryrun_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        def runner():
            try:
                self._process = subprocess.Popen(
                    cmd,
                    cwd=str(PROJECT_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                for line in self._process.stdout:
                    self.root.after(0, self._log, line.rstrip())
                self._process.wait()
                self.root.after(0, self._log, "[LAUNCHER] Syntrix parou.")
                self.root.after(0, self._on_process_end)
            except Exception as exc:
                self.root.after(0, self._log, f"[ERRO] {exc}")
                self.root.after(0, self._on_process_end)

        threading.Thread(target=runner, daemon=True).start()

    def _on_stop(self) -> None:
        """Stop running Syntrix."""
        if self._process and self._process.poll() is None:
            self._log("[LAUNCHER] Parando Syntrix...")
            self._process.terminate()

    def _on_process_end(self) -> None:
        """Re-enable buttons after process ends."""
        self.start_btn.configure(state="normal")
        self.dryrun_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def _on_analytics(self) -> None:
        """Run analytics report."""
        self._log("[LAUNCHER] Gerando relatorio de analytics...")

        def worker():
            try:
                result = subprocess.run(
                    [sys.executable, str(PROJECT_DIR / "scripts" / "analytics_report.py")],
                    cwd=str(PROJECT_DIR),
                    capture_output=True, text=True, timeout=30,
                )
                output = result.stdout or result.stderr or "Sem dados ainda."
                self.root.after(0, self._log, output.rstrip())
            except Exception as exc:
                self.root.after(0, self._log, f"[ERRO] {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_tests(self) -> None:
        """Run test suite."""
        self._log("[LAUNCHER] Rodando testes...")

        def worker():
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
                    cwd=str(PROJECT_DIR),
                    capture_output=True, text=True, timeout=60,
                )
                output = result.stdout or result.stderr
                for line in output.strip().split("\n"):
                    self.root.after(0, self._log, line)
            except Exception as exc:
                self.root.after(0, self._log, f"[ERRO] {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_config(self) -> None:
        """Open profiles.yaml in default editor."""
        config_path = PROJECT_DIR / "config" / "profiles.yaml"
        self._log(f"[LAUNCHER] Abrindo {config_path}...")
        try:
            if sys.platform == "win32":
                os.startfile(str(config_path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(config_path)])
            else:
                subprocess.Popen(["xdg-open", str(config_path)])
        except Exception as exc:
            self._log(f"[ERRO] Nao foi possivel abrir: {exc}")
            messagebox.showerror("Syntrix", f"Erro ao abrir config:\n{exc}")

    def run(self) -> None:
        """Start the launcher."""
        self.root.mainloop()


def main() -> None:
    app = SyntrixLauncher()
    app.run()


if __name__ == "__main__":
    main()
