#!/usr/bin/env python3
"""
Workflow Toolbox – Launcher

Starts the tools shipped in this repository:
- tools/daily_tracker.py (Daily Tracker)
- tools/image_automator.py   (Image Automator)

Note: This launcher does not change the tools' logic – it only starts them.
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import messagebox


ROOT_DIR = Path(__file__).resolve().parent
TOOL_DIR = ROOT_DIR / "tools"
DAILY_TRACKER = TOOL_DIR / "daily_tracker.py"
IMAGE_AUTOMATOR = TOOL_DIR / "image_automator.py"


def run_tool(script_path: Path) -> None:
    if not script_path.exists():
        messagebox.showerror("Fehler", f"Datei nicht gefunden: {script_path}")
        return

    try:
        subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(ROOT_DIR),
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
        )
    except Exception as e:
        messagebox.showerror("Startfehler", f"Konnte nicht starten:\n\n{e}")


def main() -> None:
    root = tk.Tk()
    root.title("Workflow Toolbox")
    root.geometry("640x360")
    root.configure(bg="#0f1115")

    card = tk.Frame(root, bg="#151a22", bd=0, highlightthickness=1, highlightbackground="#242b36")
    card.pack(fill="both", expand=True, padx=22, pady=22)

    tk.Label(
        card,
        text="Workflow Toolbox",
        font=("Segoe UI", 20, "bold"),
        fg="#f2f5f8",
        bg="#151a22"
    ).pack(anchor="w", padx=18, pady=(18, 2))

    tk.Label(
        card,
        text="Starte die Tools aus diesem Repo. (Desktop-Tools, Python/Tkinter)",
        font=("Segoe UI", 10),
        fg="#aab3bf",
        bg="#151a22",
        justify="left"
    ).pack(anchor="w", padx=18, pady=(0, 14))

    btn_frame = tk.Frame(card, bg="#151a22")
    btn_frame.pack(fill="x", padx=18)

    def make_btn(label: str, script_path: Path):
        b = tk.Button(
            btn_frame,
            text=label,
            font=("Segoe UI", 10, "bold"),
            fg="#0b0d10",
            bg="#0ea5a0",
            activebackground="#0f8d88",
            activeforeground="#0b0d10",
            relief="flat",
            padx=14,
            pady=10,
            command=lambda: run_tool(script_path)
        )
        b.pack(fill="x", pady=8)

    make_btn("1) Daily Tracker öffnen", DAILY_TRACKER)
    make_btn("2) Image Automator öffnen", IMAGE_AUTOMATOR)

    root.mainloop()


if __name__ == "__main__":
    main()
