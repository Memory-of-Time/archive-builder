from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..config import (
    DedupConfig,
    ExportConfig,
    NetworkConfig,
    PERFORMANCE_CDX_DELAY,
    PERFORMANCE_CDX_WORKERS,
    PERFORMANCE_REPLAY_DELAY,
    PERFORMANCE_WORKERS,
    ProjectConfig,
    SiteProfile,
    nothingtoxic_profile,
)
from ..project import project_root, safe_project_folder_name


class ProjectWizard(tk.Toplevel):
    """Modal three-step project-creation wizard."""

    PRESETS = ("NothingToxic", "Custom website")

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("New Archive Builder Project")
        self.geometry("760x700")
        self.minsize(700, 640)
        self.transient(parent)
        self.resizable(True, True)
        self.result: ProjectConfig | None = None
        self.step = 0

        self.project_name_var = tk.StringVar(value="NothingToxic")
        self.parent_dir_var = tk.StringVar(value=str(Path.home() / "Archive Builder Projects"))
        self.preset_var = tk.StringVar(value="NothingToxic")
        self.site_name_var = tk.StringVar(value="NothingToxic")
        self.domain_var = tk.StringVar(value="nothingtoxic.com")
        self.content_path_var = tk.StringVar(value="/media/")
        self.include_subdomains_var = tk.BooleanVar(value=True)
        self.from_year_var = tk.StringVar(value="1996")
        self.to_year_var = tk.StringVar(value=str(datetime.now().year))
        self.workers_var = tk.StringVar(value=str(PERFORMANCE_WORKERS))
        self.replay_delay_var = tk.StringVar(value=str(PERFORMANCE_REPLAY_DELAY))
        self.cdx_workers_var = tk.StringVar(value=str(PERFORMANCE_CDX_WORKERS))
        self.cdx_delay_var = tk.StringVar(value=str(PERFORMANCE_CDX_DELAY))
        self.day_first_var = tk.BooleanVar(value=False)
        self.xlsx_var = tk.BooleanVar(value=False)
        self.collapse_urlkey_var = tk.BooleanVar(value=True)
        self.cross_domain_dedupe_var = tk.BooleanVar(value=True)
        self.duplicate_fields_var = tk.StringVar(value="post_number,title")

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.after_idle(self._center_over_parent)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Create New Project", style="Title.TLabel").pack(anchor="w")
        self.step_label = ttk.Label(outer, text="", style="Muted.TLabel")
        self.step_label.pack(anchor="w", pady=(2, 12))
        self.body = ttk.Frame(outer)
        self.body.pack(fill="both", expand=True)
        self.pages = [ttk.Frame(self.body) for _ in range(3)]
        for page in self.pages:
            page.grid(row=0, column=0, sticky="nsew")
        self.body.rowconfigure(0, weight=1)
        self.body.columnconfigure(0, weight=1)
        self._build_project_page(self.pages[0])
        self._build_site_page(self.pages[1])
        self._build_options_page(self.pages[2])
        nav = ttk.Frame(outer)
        nav.pack(fill="x", pady=(14, 0))
        self.back_button = ttk.Button(nav, text="Back", command=self._back)
        self.back_button.pack(side="left")
        ttk.Button(nav, text="Cancel", command=self._cancel).pack(side="right")
        self.next_button = ttk.Button(nav, text="Next", style="Accent.TButton", command=self._next)
        self.next_button.pack(side="right", padx=(0, 8))
        self._show_step(0)

    def _build_project_page(self, page: ttk.Frame) -> None:
        ttk.Label(page, text="Project", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(page, text="Project name").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(page, textvariable=self.project_name_var).grid(row=1, column=1, columnspan=2, sticky="ew", pady=6, padx=(12, 0))
        ttk.Label(page, text="Save projects in").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(page, textvariable=self.parent_dir_var).grid(row=2, column=1, sticky="ew", pady=6, padx=(12, 6))
        ttk.Button(page, text="Browse…", command=self._browse_parent).grid(row=2, column=2, sticky="e", pady=6)
        ttk.Label(page, text="Archive Builder creates a dedicated folder containing its JSON, SQLite database, captures, and exports.", style="Muted.TLabel", wraplength=620).grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))
        page.columnconfigure(1, weight=1)

    def _build_site_page(self, page: ttk.Frame) -> None:
        ttk.Label(page, text="Website", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(page, text="Starter profile").grid(row=1, column=0, sticky="w", pady=5)
        preset = ttk.Combobox(page, textvariable=self.preset_var, values=self.PRESETS, state="readonly")
        preset.grid(row=1, column=1, sticky="ew", pady=5, padx=(12, 0))
        preset.bind("<<ComboboxSelected>>", self._apply_preset)
        for row, (label, variable) in enumerate((("Profile name", self.site_name_var), ("Domain", self.domain_var), ("Content path", self.content_path_var)), start=2):
            ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(page, textvariable=variable).grid(row=row, column=1, columnspan=2, sticky="ew", pady=5, padx=(12, 0))
        ttk.Checkbutton(page, text="Include all subdomains", variable=self.include_subdomains_var).grid(row=5, column=1, columnspan=2, sticky="w", pady=(8, 2), padx=(12, 0))
        ttk.Checkbutton(page, text="Ambiguous numeric Posted Dates are day-first", variable=self.day_first_var).grid(row=6, column=1, columnspan=2, sticky="w", pady=2, padx=(12, 0))
        ttk.Label(page, text="Enter example.com and /media/. No asterisk is required; the content path is treated as a prefix.", style="Muted.TLabel", wraplength=620).grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))
        page.columnconfigure(1, weight=1)

    def _build_options_page(self, page: ttk.Frame) -> None:
        ttk.Label(page, text="Archive options", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        items = (
            ("From capture year", self.from_year_var),
            ("To capture year", self.to_year_var),
            ("Combined replay workers", self.workers_var),
            ("Replay start spacing (seconds)", self.replay_delay_var),
            ("CDX workers", self.cdx_workers_var),
            ("CDX start spacing (seconds)", self.cdx_delay_var),
        )
        for row, (label, variable) in enumerate(items, start=1):
            ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(page, textvariable=variable, width=18).grid(row=row, column=1, sticky="w", pady=4, padx=(12, 0))
        ttk.Button(page, text="Apply performance preset", command=self._apply_performance_preset).grid(row=3, column=2, rowspan=2, sticky="w", padx=(16, 0))
        ttk.Checkbutton(page, text="Collapse repeated CDX captures with collapse=urlkey", variable=self.collapse_urlkey_var).grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 2))
        ttk.Checkbutton(page, text="Detect duplicate posts across hostnames/subdomains", variable=self.cross_domain_dedupe_var).grid(row=8, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(page, text="Also export optional A–Z formatted XLSX workbook", variable=self.xlsx_var).grid(row=9, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(page, text="Duplicate match fields").grid(row=10, column=0, sticky="w", pady=4)
        ttk.Entry(page, textvariable=self.duplicate_fields_var, width=28).grid(row=10, column=1, sticky="w", pady=4, padx=(12, 0))
        ttk.Label(page, text="The performance preset is 10 combined workers, 0.125 s replay spacing, 10 CDX workers, and 0.75 s CDX spacing. Exact duplicate matches auto-merge; weaker conflicts require review.", style="Muted.TLabel", wraplength=640).grid(row=11, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.summary_var = tk.StringVar()
        ttk.Label(page, textvariable=self.summary_var, wraplength=640).grid(row=12, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _apply_performance_preset(self) -> None:
        self.workers_var.set(str(PERFORMANCE_WORKERS))
        self.replay_delay_var.set(str(PERFORMANCE_REPLAY_DELAY))
        self.cdx_workers_var.set(str(PERFORMANCE_CDX_WORKERS))
        self.cdx_delay_var.set(str(PERFORMANCE_CDX_DELAY))
        self._update_summary()

    def _center_over_parent(self) -> None:
        parent = self.master
        try:
            x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_width()) // 2)
            y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_height()) // 2)
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def _browse_parent(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Choose where Archive Builder projects are stored")
        if path:
            self.parent_dir_var.set(path)

    def _apply_preset(self, _event=None) -> None:
        if self.preset_var.get() == "NothingToxic":
            profile = nothingtoxic_profile()
            self.site_name_var.set(profile.name)
            self.domain_var.set(profile.domain)
            self.content_path_var.set(profile.content_path)
            self.include_subdomains_var.set(profile.include_subdomains)
            self.from_year_var.set(str(profile.from_year))
            self.to_year_var.set(str(profile.to_year))
            self.day_first_var.set(profile.day_first_dates)
            if not self.project_name_var.get().strip() or self.project_name_var.get().strip() == "Custom Website":
                self.project_name_var.set("NothingToxic")
        else:
            self.site_name_var.set("Custom Website")
            self.domain_var.set("")
            self.content_path_var.set("/")
            self.include_subdomains_var.set(True)
            if self.project_name_var.get().strip() == "NothingToxic":
                self.project_name_var.set("Custom Website")

    def _validate_step(self, step: int) -> None:
        if step == 0:
            safe_project_folder_name(self.project_name_var.get())
            parent_text = self.parent_dir_var.get().strip()
            if not parent_text:
                raise ValueError("choose where the project should be saved")
            root = project_root(Path(parent_text).expanduser(), self.project_name_var.get())
            if (root / "archive_builder.json").exists() or (root / "archive_builder.sqlite3").exists():
                raise FileExistsError(f"An Archive Builder project already exists at:\n{root}")
        elif step == 1:
            self._site_profile()
        elif step == 2:
            self._build_config()

    def _site_profile(self) -> SiteProfile:
        base = nothingtoxic_profile()
        return SiteProfile(
            name=self.site_name_var.get(), domain=self.domain_var.get(),
            include_subdomains=self.include_subdomains_var.get(), content_path=self.content_path_var.get(),
            from_year=int(self.from_year_var.get()), to_year=int(self.to_year_var.get()),
            day_first_dates=self.day_first_var.get(), rules=base.rules,
        ).normalized()

    def _build_config(self) -> ProjectConfig:
        root = project_root(Path(self.parent_dir_var.get()), self.project_name_var.get())
        site = self._site_profile()
        network = NetworkConfig(
            workers=int(self.workers_var.get()), download_delay=float(self.replay_delay_var.get()),
            cdx_workers=int(self.cdx_workers_var.get()), cdx_delay=float(self.cdx_delay_var.get()),
        ).normalized()
        fields = tuple(part.strip() for part in self.duplicate_fields_var.get().split(",") if part.strip())
        dedup = DedupConfig(
            cdx_collapse_urlkey=self.collapse_urlkey_var.get(),
            cross_domain_enabled=self.cross_domain_dedupe_var.get(), match_fields=fields,
            preferred_domain=site.domain,
        ).normalized()
        export = ExportConfig(create_xlsx_presentation=self.xlsx_var.get()).normalized()
        return ProjectConfig(output_dir=root, site=site, network=network, dedup=dedup, export=export).normalized()

    def _update_summary(self) -> None:
        try:
            cfg = self._build_config()
            self.summary_var.set(
                f"Project folder: {cfg.output_dir}\n"
                f"Target: {cfg.site.domain}{cfg.site.content_path} | Subdomains: {'yes' if cfg.site.include_subdomains else 'no'} | "
                f"Years: {cfg.site.from_year}–{cfg.site.to_year}\n"
                f"Replay: {cfg.network.workers} workers @ {cfg.network.download_delay:g}s | "
                f"CDX: {cfg.network.cdx_workers} workers @ {cfg.network.cdx_delay:g}s | "
                f"XLSX: {'yes' if cfg.export.create_xlsx_presentation else 'no'}"
            )
        except Exception:
            self.summary_var.set("")

    def _show_step(self, step: int) -> None:
        self.step = step
        self.pages[step].tkraise()
        names = ("Project location", "Website target", "Archive options")
        self.step_label.configure(text=f"Step {step + 1} of 3 — {names[step]}")
        self.back_button.configure(state="normal" if step else "disabled")
        self.next_button.configure(text="Create Project" if step == 2 else "Next")
        if step == 2:
            self._update_summary()

    def _next(self) -> None:
        try:
            self._validate_step(self.step)
        except Exception as exc:
            messagebox.showerror("Archive Builder", str(exc), parent=self)
            return
        if self.step < 2:
            self._show_step(self.step + 1)
            return
        self.result = self._build_config()
        self.grab_release()
        self.destroy()

    def _back(self) -> None:
        if self.step:
            self._show_step(self.step - 1)

    def _cancel(self) -> None:
        self.result = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self) -> ProjectConfig | None:
        self.wait_window()
        return self.result
