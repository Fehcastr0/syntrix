"""
login.py — Syntrix Login App.

Simple Tkinter GUI to:
- Enter IQ Option email and password
- Test connection to the broker
- Save credentials to .env file for future use
- Select account type (Demo/Real)
- Show balances for both account types
"""

from __future__ import annotations

import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


def ensure_dependencies() -> None:
    """Auto-install required dependencies if missing."""
    for module, package in [("fake_useragent", "fake_useragent")]:
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

ENV_FILE = ".env"


def load_env() -> dict:
    """Load existing .env values."""
    env = {}
    path = Path(ENV_FILE)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def save_env(email: str, password: str, practice: bool,
             amount: float = 2.0, duration: int = 60) -> None:
    """Save credentials and trade settings to .env file."""
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(f"IQ_EMAIL={email}\n")
        f.write(f"IQ_PASSWORD={password}\n")
        f.write(f"IQ_PRACTICE={'true' if practice else 'false'}\n")
        f.write(f"IQ_AMOUNT={amount}\n")
        f.write(f"IQ_DURATION={duration}\n")


def try_connect(email: str, password: str):
    """Try to connect to IQ Option. Returns (success, data_dict)."""
    from iq_api import get_iq_option_class

    IQ_Option, error = get_iq_option_class()
    if IQ_Option is None:
        return False, {"error": error}

    try:
        api = IQ_Option(email, password)
        check, reason = api.connect()
        if check:
            # Get demo balance
            api.change_balance("PRACTICE")
            demo_balance = api.get_balance()

            # Get real balance
            api.change_balance("REAL")
            real_balance = api.get_balance()

            try:
                api.disconnect()
            except (AttributeError, Exception):
                pass
            return True, {
                "demo_balance": demo_balance,
                "real_balance": real_balance,
            }
        else:
            try:
                api.disconnect()
            except (AttributeError, Exception):
                pass
            return False, {"error": f"Falha na conexao: {reason}"}
    except Exception as exc:
        return False, {"error": f"Erro: {exc}"}


class LoginApp:
    """Tkinter login window for Syntrix."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Syntrix — Login IQ Option")
        self.root.geometry("450x620")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        self._build_ui()
        self._load_saved()

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"),
                        foreground="#e94560", background="#1a1a2e")
        style.configure("Sub.TLabel", font=("Segoe UI", 9),
                        foreground="#aaaaaa", background="#1a1a2e")
        style.configure("Field.TLabel", font=("Segoe UI", 10),
                        foreground="#ffffff", background="#1a1a2e")
        style.configure("Status.TLabel", font=("Segoe UI", 10),
                        foreground="#aaaaaa", background="#1a1a2e")
        style.configure("Balance.TLabel", font=("Segoe UI", 11, "bold"),
                        foreground="#00d2d3", background="#16213e")
        style.configure("BalanceTitle.TLabel", font=("Segoe UI", 9),
                        foreground="#aaaaaa", background="#16213e")
        style.configure("BalanceVal.TLabel", font=("Segoe UI", 14, "bold"),
                        foreground="#00d2d3", background="#16213e")
        style.configure("TFrame", background="#1a1a2e")
        style.configure("Balance.TFrame", background="#16213e")
        style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"),
                        foreground="#ffffff", background="#e94560")
        style.map("Accent.TButton",
                  background=[("active", "#c81e45"), ("disabled", "#555555")])
        style.configure("Save.TButton", font=("Segoe UI", 10),
                        foreground="#ffffff", background="#0f3460")
        style.map("Save.TButton",
                  background=[("active", "#16213e")])
        style.configure("Run.TButton", font=("Segoe UI", 10, "bold"),
                        foreground="#ffffff", background="#27ae60")
        style.map("Run.TButton",
                  background=[("active", "#219a52"), ("disabled", "#555555")])

        main = ttk.Frame(self.root, padding=30)
        main.pack(fill="both", expand=True)

        # Title
        ttk.Label(main, text="SYNTRIX", style="Title.TLabel").pack(pady=(0, 2))
        ttk.Label(main, text="Login IQ Option", style="Sub.TLabel").pack(pady=(0, 20))

        # Email
        ttk.Label(main, text="Email:", style="Field.TLabel").pack(anchor="w")
        self.email_var = tk.StringVar()
        self.email_entry = ttk.Entry(main, textvariable=self.email_var, width=40,
                                      font=("Segoe UI", 10))
        self.email_entry.pack(fill="x", pady=(2, 10))

        # Password
        ttk.Label(main, text="Senha:", style="Field.TLabel").pack(anchor="w")
        self.pass_var = tk.StringVar()
        self.pass_entry = ttk.Entry(main, textvariable=self.pass_var, show="*",
                                     width=40, font=("Segoe UI", 10))
        self.pass_entry.pack(fill="x", pady=(2, 10))

        # Account type
        type_frame = ttk.Frame(main)
        type_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(type_frame, text="Conta para operar:", style="Field.TLabel").pack(side="left")
        self.account_var = tk.StringVar(value="demo")
        ttk.Radiobutton(type_frame, text="Demo", variable=self.account_var,
                         value="demo").pack(side="left", padx=(10, 5))
        ttk.Radiobutton(type_frame, text="Real", variable=self.account_var,
                         value="real").pack(side="left")

        # Trade settings
        trade_frame = ttk.Frame(main)
        trade_frame.pack(fill="x", pady=(0, 15))

        # Amount
        amount_col = ttk.Frame(trade_frame)
        amount_col.pack(side="left", expand=True, fill="x", padx=(0, 5))
        ttk.Label(amount_col, text="Valor da entrada ($):", style="Field.TLabel").pack(anchor="w")
        self.amount_var = tk.StringVar(value="2.00")
        ttk.Entry(amount_col, textvariable=self.amount_var, width=10,
                  font=("Segoe UI", 10)).pack(fill="x", pady=(2, 0))

        # Duration
        duration_col = ttk.Frame(trade_frame)
        duration_col.pack(side="left", expand=True, fill="x", padx=(5, 0))
        ttk.Label(duration_col, text="Duracao (segundos):", style="Field.TLabel").pack(anchor="w")
        self.duration_var = tk.StringVar(value="60")
        ttk.Entry(duration_col, textvariable=self.duration_var, width=10,
                  font=("Segoe UI", 10)).pack(fill="x", pady=(2, 0))

        # Buttons
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(0, 10))

        self.connect_btn = ttk.Button(btn_frame, text="Conectar & Testar",
                                       style="Accent.TButton", command=self._on_connect)
        self.connect_btn.pack(side="left", expand=True, fill="x", padx=(0, 5))

        self.save_btn = ttk.Button(btn_frame, text="Salvar",
                                    style="Save.TButton", command=self._on_save)
        self.save_btn.pack(side="left", expand=True, fill="x", padx=(5, 0))

        # Balance display
        balance_frame = ttk.Frame(main, style="Balance.TFrame", padding=15)
        balance_frame.pack(fill="x", pady=(10, 10))

        bal_row = ttk.Frame(balance_frame, style="Balance.TFrame")
        bal_row.pack(fill="x")

        # Demo balance
        demo_col = ttk.Frame(bal_row, style="Balance.TFrame")
        demo_col.pack(side="left", expand=True)
        ttk.Label(demo_col, text="DEMO", style="BalanceTitle.TLabel").pack()
        self.demo_bal_var = tk.StringVar(value="—")
        ttk.Label(demo_col, textvariable=self.demo_bal_var, style="BalanceVal.TLabel").pack()

        # Separator
        sep = ttk.Frame(bal_row, style="Balance.TFrame", width=2)
        sep.pack(side="left", fill="y", padx=10)

        # Real balance
        real_col = ttk.Frame(bal_row, style="Balance.TFrame")
        real_col.pack(side="left", expand=True)
        ttk.Label(real_col, text="REAL", style="BalanceTitle.TLabel").pack()
        self.real_bal_var = tk.StringVar(value="—")
        ttk.Label(real_col, textvariable=self.real_bal_var, style="BalanceVal.TLabel").pack()

        # Run Syntrix button
        self.run_btn = ttk.Button(main, text="Iniciar Syntrix",
                                   style="Run.TButton", command=self._on_run)
        self.run_btn.pack(fill="x", pady=(5, 5))
        self.run_btn.configure(state="disabled")

        # Status
        self.status_var = tk.StringVar(value="Aguardando...")
        self.status_label = ttk.Label(main, textvariable=self.status_var,
                                       style="Status.TLabel", wraplength=380)
        self.status_label.pack(pady=(5, 0))

    def _load_saved(self) -> None:
        """Load saved credentials from .env."""
        env = load_env()
        if "IQ_EMAIL" in env:
            self.email_var.set(env["IQ_EMAIL"])
        if "IQ_PASSWORD" in env:
            self.pass_var.set(env["IQ_PASSWORD"])
        if env.get("IQ_PRACTICE", "true").lower() == "false":
            self.account_var.set("real")
        if "IQ_AMOUNT" in env:
            self.amount_var.set(env["IQ_AMOUNT"])
        if "IQ_DURATION" in env:
            self.duration_var.set(env["IQ_DURATION"])
        if env.get("IQ_EMAIL"):
            self.status_var.set("Credenciais carregadas do .env")

    def _on_connect(self) -> None:
        """Test connection in background thread."""
        email = self.email_var.get().strip()
        password = self.pass_var.get().strip()

        if not email or not password:
            self.status_var.set("Preencha email e senha!")
            return

        self.connect_btn.configure(state="disabled")
        self.status_var.set("Conectando...")
        self.demo_bal_var.set("...")
        self.real_bal_var.set("...")

        def worker():
            success, data = try_connect(email, password)
            self.root.after(0, lambda: self._connection_result(success, data))

        threading.Thread(target=worker, daemon=True).start()

    def _connection_result(self, success: bool, data: dict) -> None:
        """Handle connection result on main thread."""
        self.connect_btn.configure(state="normal")
        if success:
            demo = data["demo_balance"]
            real = data["real_balance"]
            self.demo_bal_var.set(f"${demo:,.2f}")
            self.real_bal_var.set(f"${real:,.2f}")
            self.status_var.set("Conectado com sucesso! Credenciais salvas.")
            self.run_btn.configure(state="normal")
            self._on_save()
            messagebox.showinfo(
                "Syntrix",
                f"Login OK!\n\n"
                f"Saldo Demo: ${demo:,.2f}\n"
                f"Saldo Real: ${real:,.2f}\n\n"
                f"Credenciais salvas no .env"
            )
        else:
            self.demo_bal_var.set("—")
            self.real_bal_var.set("—")
            self.status_var.set(data.get("error", "Erro desconhecido"))
            self.run_btn.configure(state="disabled")
            messagebox.showerror("Syntrix", data.get("error", "Erro desconhecido"))

    def _on_save(self) -> None:
        """Save credentials and trade settings to .env."""
        email = self.email_var.get().strip()
        password = self.pass_var.get().strip()
        practice = self.account_var.get() == "demo"

        if not email or not password:
            self.status_var.set("Preencha email e senha!")
            return

        try:
            amount = float(self.amount_var.get().strip())
        except ValueError:
            amount = 2.0
        try:
            duration = int(self.duration_var.get().strip())
        except ValueError:
            duration = 60

        save_env(email, password, practice, amount, duration)
        self.status_var.set("Credenciais e configuracoes salvas em .env")

    def _on_run(self) -> None:
        """Launch Syntrix main system."""
        import subprocess
        import sys

        self.status_var.set("Iniciando Syntrix...")
        self.root.destroy()
        subprocess.Popen([sys.executable, "main.py"])

    def run(self) -> None:
        """Start the login window."""
        self.root.mainloop()


def main() -> None:
    app = LoginApp()
    app.run()


if __name__ == "__main__":
    main()
