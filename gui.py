"""
ABCP Price Scanner — графический интерфейс.
Запуск:  python gui.py
"""

import asyncio
import glob
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from dotenv import load_dotenv, set_key


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ABCP Price Scanner")
        self.resizable(True, True)
        self.minsize(620, 540)

        self._running = False
        self._thread: threading.Thread | None = None

        self._base_dir = os.path.dirname(os.path.abspath(__file__))
        self._env_path = os.path.join(self._base_dir, ".env")
        load_dotenv(self._env_path)

        self._build_ui()
        self._load_env()

    # ------------------------------------------------------------------ UI ---

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # --- API settings ---
        frm_api = ttk.LabelFrame(self, text="Настройки API")
        frm_api.pack(fill="x", padx=10, pady=6)

        ttk.Label(frm_api, text="URL:").grid(row=0, column=0, sticky="e", **pad)
        self.var_url = tk.StringVar()
        ttk.Entry(frm_api, textvariable=self.var_url, width=50).grid(
            row=0, column=1, columnspan=2, sticky="ew", **pad
        )

        ttk.Label(frm_api, text="Логин:").grid(row=1, column=0, sticky="e", **pad)
        self.var_login = tk.StringVar()
        ttk.Entry(frm_api, textvariable=self.var_login, width=30).grid(
            row=1, column=1, sticky="ew", **pad
        )

        ttk.Label(frm_api, text="MD5 пароль:").grid(row=2, column=0, sticky="e", **pad)
        self.var_psw = tk.StringVar()
        ttk.Entry(frm_api, textvariable=self.var_psw, width=40, show="*").grid(
            row=2, column=1, sticky="ew", **pad
        )
        ttk.Button(frm_api, text="Сохранить", command=self._save_env).grid(
            row=2, column=2, **pad
        )

        frm_api.columnconfigure(1, weight=1)

        # --- Paths ---
        frm_paths = ttk.LabelFrame(self, text="Папки")
        frm_paths.pack(fill="x", padx=10, pady=4)

        ttk.Label(frm_paths, text="Входная:").grid(row=0, column=0, sticky="e", **pad)
        self.var_input = tk.StringVar(
            value=os.path.join(self._base_dir, "input")
        )
        ttk.Entry(frm_paths, textvariable=self.var_input, width=50).grid(
            row=0, column=1, sticky="ew", **pad
        )
        ttk.Button(
            frm_paths, text="Обзор",
            command=lambda: self._browse(self.var_input)
        ).grid(row=0, column=2, **pad)

        ttk.Label(frm_paths, text="Выходная:").grid(row=1, column=0, sticky="e", **pad)
        self.var_output = tk.StringVar(
            value=os.path.join(self._base_dir, "output")
        )
        ttk.Entry(frm_paths, textvariable=self.var_output, width=50).grid(
            row=1, column=1, sticky="ew", **pad
        )
        ttk.Button(
            frm_paths, text="Обзор",
            command=lambda: self._browse(self.var_output)
        ).grid(row=1, column=2, **pad)

        frm_paths.columnconfigure(1, weight=1)

        # --- Options ---
        frm_opts = ttk.LabelFrame(self, text="Параметры")
        frm_opts.pack(fill="x", padx=10, pady=4)

        ttk.Label(frm_opts, text="Параллельность:").grid(
            row=0, column=0, sticky="e", **pad
        )
        self.var_conc = tk.IntVar(value=6)
        ttk.Spinbox(
            frm_opts, from_=1, to=30, textvariable=self.var_conc, width=6
        ).grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(frm_opts, text="Таймаут (сек):").grid(
            row=0, column=2, sticky="e", **pad
        )
        self.var_timeout = tk.IntVar(value=30)
        ttk.Spinbox(
            frm_opts, from_=5, to=120, textvariable=self.var_timeout, width=6
        ).grid(row=0, column=3, sticky="w", **pad)

        # --- Start button ---
        self.btn_start = ttk.Button(
            self, text="▶  СТАРТ", command=self._start
        )
        self.btn_start.pack(pady=10, ipadx=24, ipady=8)

        # --- Progress ---
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=10)

        # --- Log ---
        frm_log = ttk.LabelFrame(self, text="Лог")
        frm_log.pack(fill="both", expand=True, padx=10, pady=6)
        self.log_text = scrolledtext.ScrolledText(
            frm_log, height=10, state="disabled", font=("Consolas", 9)
        )
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

    # --------------------------------------------------------- .env helpers ---

    def _load_env(self):
        self.var_url.set(os.getenv("ABCP_BASE_URL", ""))
        self.var_login.set(os.getenv("ABCP_LOGIN", ""))
        self.var_psw.set(os.getenv("ABCP_PASSWORD_MD5", ""))

    def _save_env(self):
        set_key(self._env_path, "ABCP_BASE_URL", self.var_url.get().strip())
        set_key(self._env_path, "ABCP_LOGIN", self.var_login.get().strip())
        set_key(self._env_path, "ABCP_PASSWORD_MD5", self.var_psw.get().strip())
        self._log("Настройки сохранены в .env\n")

    def _browse(self, var: tk.StringVar):
        directory = filedialog.askdirectory(initialdir=var.get())
        if directory:
            var.set(directory)

    # ------------------------------------------------------------ logging ---

    def _log(self, text: str):
        """Thread-safe запись в текстовое поле."""
        def _update():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", text)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(0, _update)

    # ---------------------------------------------------------- run logic ---

    def _start(self):
        if self._running:
            return

        base_url = self.var_url.get().strip()
        userlogin = self.var_login.get().strip()
        userpsw_md5 = self.var_psw.get().strip()

        if not base_url or not userlogin or not userpsw_md5:
            messagebox.showerror("Ошибка", "Заполните URL, логин и MD5-пароль")
            return

        if not base_url.startswith("http"):
            base_url = "https://" + base_url

        in_dir = self.var_input.get().strip()
        out_dir = self.var_output.get().strip()
        files = sorted(glob.glob(os.path.join(in_dir, "*.xlsx")))
        if not files:
            messagebox.showerror("Ошибка", f"В папке '{in_dir}' нет .xlsx файлов")
            return

        self._running = True
        self.btn_start.configure(state="disabled", text="⏳  Выполняется...")
        self.progress.start(12)
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._log(f"Найдено файлов: {len(files)}\n")

        self._thread = threading.Thread(
            target=self._worker,
            args=(files, out_dir, base_url, userlogin, userpsw_md5,
                  self.var_conc.get(), self.var_timeout.get()),
            daemon=True,
        )
        self._thread.start()

    def _worker(
        self, files, out_dir, base_url, userlogin,
        userpsw_md5, concurrency, timeout_s
    ):
        from price_scan_abcp import process_one_excel

        class _LogWriter:
            def __init__(self, app: "App"):
                self._app = app
            def write(self, s: str):
                if s.strip():
                    self._app._log(s if s.endswith("\n") else s + "\n")
            def flush(self):
                pass

        old_stdout, old_stderr = sys.stdout, sys.stderr
        writer = _LogWriter(self)
        sys.stdout = writer
        sys.stderr = writer

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def runner():
                for f in files:
                    self._log(f"\n→ Обрабатываю: {os.path.basename(f)}\n")
                    out = await process_one_excel(
                        in_path=f,
                        out_dir=out_dir,
                        base_url=base_url,
                        userlogin=userlogin,
                        userpsw_md5=userpsw_md5,
                        concurrency=max(1, concurrency),
                        timeout_s=max(5, timeout_s),
                    )
                    if out:
                        self._log(f"OK: {out}\n")
                    else:
                        self._log("Файл обработан, но результатов нет.\n")

            loop.run_until_complete(runner())
            loop.close()
            self._log("\nВсе файлы обработаны!\n")
        except Exception as exc:
            self._log(f"\nОшибка: {exc}\n")
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            self.after(0, self._done)

    def _done(self):
        self._running = False
        self.progress.stop()
        self.btn_start.configure(state="normal", text="▶  СТАРТ")


if __name__ == "__main__":
    app = App()
    app.mainloop()
