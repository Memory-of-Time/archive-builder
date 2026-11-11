from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import ttk

LIGHT = {
    "bg": "#f4f6f8", "panel": "#ffffff", "sidebar": "#172033", "sidebar_hover": "#26344f",
    "sidebar_text": "#f7f9fc", "text": "#18202d", "muted": "#667085", "border": "#d0d5dd",
    "accent": "#2563eb", "accent_active": "#1d4ed8", "input": "#ffffff",
}
DARK = {
    "bg": "#111827", "panel": "#182235", "sidebar": "#0b1220", "sidebar_hover": "#24324a",
    "sidebar_text": "#f8fafc", "text": "#e5e7eb", "muted": "#9ca3af", "border": "#344054",
    "accent": "#60a5fa", "accent_active": "#93c5fd", "input": "#101827",
}


def detect_system_theme(root: tk.Misc | None = None) -> str:
    env = os.environ.get("ARCHIVE_BUILDER_THEME", "").strip().casefold()
    if env in {"light", "dark"}:
        return env
    if sys.platform == "darwin" and root is not None:
        try:
            return "dark" if str(root.tk.call("exec", "defaults", "read", "-g", "AppleInterfaceStyle")).casefold() == "dark" else "light"
        except Exception:
            pass
    return "light"


def apply_theme(root: tk.Tk, requested: str = "system") -> dict[str, str]:
    name = detect_system_theme(root) if requested == "system" else requested
    colors = DARK if name == "dark" else LIGHT
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(background=colors["bg"])
    style.configure(".", background=colors["bg"], foreground=colors["text"])
    style.configure("TFrame", background=colors["bg"])
    style.configure("Panel.TFrame", background=colors["panel"])
    style.configure("TLabel", background=colors["bg"], foreground=colors["text"])
    style.configure("Panel.TLabel", background=colors["panel"], foreground=colors["text"])
    style.configure("Muted.TLabel", background=colors["bg"], foreground=colors["muted"])
    style.configure("Title.TLabel", background=colors["bg"], foreground=colors["text"], font=("TkDefaultFont", 20, "bold"))
    style.configure("Section.TLabel", background=colors["bg"], foreground=colors["text"], font=("TkDefaultFont", 12, "bold"))
    style.configure("TButton", padding=(10, 6), background=colors["panel"], foreground=colors["text"])
    style.configure("Accent.TButton", background=colors["accent"], foreground="#ffffff", font=("TkDefaultFont", 10, "bold"))
    style.map("Accent.TButton", background=[("active", colors["accent_active"])])
    style.configure("TEntry", fieldbackground=colors["input"], foreground=colors["text"])
    style.configure("TCombobox", fieldbackground=colors["input"], foreground=colors["text"])
    style.configure("Treeview", background=colors["panel"], fieldbackground=colors["panel"], foreground=colors["text"], rowheight=25)
    style.map("Treeview", background=[("selected", colors["accent"])], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", font=("TkDefaultFont", 10, "bold"))
    style.configure("Horizontal.TProgressbar", background=colors["accent"], troughcolor=colors["border"])
    return colors
