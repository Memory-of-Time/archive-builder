from __future__ import annotations

import queue
import sqlite3
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..builder.preview import preview_page
from ..builder.workflow import run_operation
from ..builder.deduplicate import duplicate_counts, set_duplicate_canonical, set_duplicate_decision
from ..builder.export import preview_logical_rows
from ..config import (
    DedupConfig, ExportConfig, ExtractionRule, NetworkConfig, ProjectConfig,
    PERFORMANCE_CDX_DELAY, PERFORMANCE_CDX_WORKERS, PERFORMANCE_REPLAY_DELAY, PERFORMANCE_WORKERS,
    load_project_config, new_project_config, save_project_config,
)
from ..project import create_project_scaffold
from ..constants import APP_NAME, VERSION, CONFIG_NAME, FIELD_LABELS, FIELD_NAMES
from ..database.connection import open_database
from ..database.repositories import page_counts
from ..events import ProgressEvent, Stopped
from .event_queue import CoalescingEventQueue
from .project_wizard import ProjectWizard
from .theme import apply_theme


class ArchiveBuilderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}")
        self.geometry("1120x760")
        self.minsize(960, 680)
        self.colors = apply_theme(self)
        self.config_obj: ProjectConfig | None = None
        self.database: sqlite3.Connection | None = None
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.events = CoalescingEventQueue()
        self._build_ui()
        self.after(100, self._drain_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Archive Builder", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Build structured chronological datasets from archived websites.", style="Muted.TLabel").pack(anchor="w", pady=(0, 12))

        project_row = ttk.Frame(outer)
        project_row.pack(fill="x", pady=(0, 10))
        self.project_var = tk.StringVar()
        ttk.Label(project_row, text="Project").pack(side="left")
        ttk.Entry(project_row, textvariable=self.project_var).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(project_row, text="New…", command=self.new_project).pack(side="left", padx=2)
        ttk.Button(project_row, text="Open…", command=self.open_project).pack(side="left", padx=2)
        ttk.Button(project_row, text="Save", command=self.save_project).pack(side="left", padx=2)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        self.site_tab = ttk.Frame(notebook, padding=12)
        self.rules_tab = ttk.Frame(notebook, padding=12)
        self.preview_tab = ttk.Frame(notebook, padding=12)
        self.duplicates_tab = ttk.Frame(notebook, padding=12)
        self.results_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.site_tab, text="Site & Build")
        notebook.add(self.rules_tab, text="Extraction Rules")
        notebook.add(self.preview_tab, text="Preview")
        notebook.add(self.duplicates_tab, text="Duplicates")
        notebook.add(self.results_tab, text="Results")
        self._build_site_tab()
        self._build_rules_tab()
        self._build_preview_tab()
        self._build_duplicates_tab()
        self._build_results_tab()

        status = ttk.Frame(outer, padding=(0, 10, 0, 0))
        status.pack(side="bottom", before=notebook, fill="x")
        self.status_var = tk.StringVar(value="Create or open a project to begin.")
        ttk.Label(status, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(status, mode="determinate", length=260)
        self.progress.pack(side="right")

    def _build_site_tab(self) -> None:
        self.site_canvas = tk.Canvas(self.site_tab, highlightthickness=0, background=self.colors["bg"])
        scrollbar = ttk.Scrollbar(self.site_tab, orient="vertical", command=self.site_canvas.yview)
        self.site_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.site_canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(self.site_canvas)
        window = self.site_canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _event: self.site_canvas.configure(scrollregion=self.site_canvas.bbox("all")))
        self.site_canvas.bind("<Configure>", lambda event: self.site_canvas.itemconfigure(window, width=event.width))
        form = ttk.Frame(body)
        form.pack(fill="x")
        self.site_name_var = tk.StringVar(value="NothingToxic")
        self.domain_var = tk.StringVar(value="nothingtoxic.com")
        self.subdomains_var = tk.BooleanVar(value=True)
        self.path_var = tk.StringVar(value="/media/")
        self.from_year_var = tk.StringVar(value="1996")
        self.to_year_var = tk.StringVar()
        self.workers_var = tk.StringVar(value=str(PERFORMANCE_WORKERS))
        self.replay_delay_var = tk.StringVar(value=str(PERFORMANCE_REPLAY_DELAY))
        self.cdx_workers_var = tk.StringVar(value=str(PERFORMANCE_CDX_WORKERS))
        self.cdx_delay_var = tk.StringVar(value=str(PERFORMANCE_CDX_DELAY))
        self.day_first_var = tk.BooleanVar(value=False)
        self.xlsx_var = tk.BooleanVar(value=False)
        self.collapse_urlkey_var = tk.BooleanVar(value=True)
        self.cross_domain_dedupe_var = tk.BooleanVar(value=True)
        self.duplicate_fields_var = tk.StringVar(value="post_number,title")
        self.preferred_domain_var = tk.StringVar(value="nothingtoxic.com")
        fields = [
            ("Site profile name", self.site_name_var),
            ("Domain", self.domain_var),
            ("Content path", self.path_var),
            ("From capture year", self.from_year_var),
            ("To capture year", self.to_year_var),
            ("Combined replay workers", self.workers_var),
            ("Replay start spacing (seconds)", self.replay_delay_var),
            ("CDX workers", self.cdx_workers_var),
            ("CDX start spacing (seconds)", self.cdx_delay_var),
        ]
        for row, (label, var) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
            ttk.Entry(form, textvariable=var, width=52).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(form, text="Include all subdomains", variable=self.subdomains_var).grid(row=1, column=2, sticky="w", padx=12)
        ttk.Checkbutton(form, text="Day-first ambiguous Posted Dates", variable=self.day_first_var).grid(row=3, column=2, sticky="w", padx=12)
        ttk.Checkbutton(form, text="Optional A–Z XLSX export", variable=self.xlsx_var).grid(row=4, column=2, sticky="w", padx=12)
        ttk.Button(form, text="Apply performance preset", command=self.apply_performance_preset).grid(row=5, column=2, rowspan=2, sticky="w", padx=12)
        form.columnconfigure(1, weight=1)
        ttk.Label(
            body,
            text="NothingToxic starts as domain + subdomains filtered to /media/. Change these settings to reuse Archive Builder on other sites.",
            style="Muted.TLabel",
            wraplength=900,
        ).pack(anchor="w", pady=(12, 6))

        dedupe = ttk.LabelFrame(body, text="Deduplication", padding=10)
        dedupe.pack(fill="x", pady=(4, 8))
        ttk.Checkbutton(dedupe, text="CDX collapse=urlkey (collapse repeated captures of the same URL)", variable=self.collapse_urlkey_var).grid(row=0, column=0, columnspan=4, sticky="w", pady=2)
        ttk.Checkbutton(dedupe, text="Detect duplicate posts across hostnames/subdomains", variable=self.cross_domain_dedupe_var).grid(row=1, column=0, columnspan=4, sticky="w", pady=2)
        ttk.Label(dedupe, text="Match fields").grid(row=2, column=0, sticky="w", pady=(6, 2))
        ttk.Entry(dedupe, textvariable=self.duplicate_fields_var, width=28).grid(row=2, column=1, sticky="w", padx=(8, 18), pady=(6, 2))
        ttk.Label(dedupe, text="Preferred domain").grid(row=2, column=2, sticky="w", pady=(6, 2))
        ttk.Entry(dedupe, textvariable=self.preferred_domain_var, width=26).grid(row=2, column=3, sticky="ew", padx=(8, 0), pady=(6, 2))
        dedupe.columnconfigure(3, weight=1)
        ttk.Label(dedupe, text="Example: post_number,title. Exact cross-host matches auto-merge; weaker same-anchor matches are marked probable for review.", style="Muted.TLabel", wraplength=850).grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=8)
        for text, mode in (("Index URLs", "index"), ("Download Pages", "download"), ("Extract Fields", "extract"), ("Analyze Duplicates", "dedupe"), ("Export CSV", "export")):
            ttk.Button(buttons, text=text, command=lambda m=mode: self.start_operation(m)).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Build All", style="Accent.TButton", command=lambda: self.start_operation("all")).pack(side="left", padx=(8, 6))
        ttk.Button(buttons, text="Stop", command=self.stop_operation).pack(side="left")
        self.stats_var = tk.StringVar(value="Indexed: 0 | Downloadable: 0 | Downloaded: 0 | Extracted: 0 | Errors: 0")
        ttk.Label(body, textvariable=self.stats_var, style="Section.TLabel").pack(anchor="w", pady=(12, 2))
        self.duplicate_stats_var = tk.StringVar(value="Duplicate groups: 0 | Exact: 0 | Probable: 0 | Needs review: 0")
        ttk.Label(body, textvariable=self.duplicate_stats_var, style="Muted.TLabel").pack(anchor="w", pady=(0, 4))

        def scroll(event):
            bounds = self.site_canvas.bbox("all")
            if bounds and bounds[3] > self.site_canvas.winfo_height():
                number = getattr(event, "num", None)
                delta = getattr(event, "delta", 0)
                direction = -1 if number == 4 or delta > 0 else 1
                self.site_canvas.yview_scroll(direction, "units")
                return "break"
            return None

        pending = [self.site_canvas, body]
        while pending:
            widget = pending.pop()
            for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(event, scroll, add="+")
            pending.extend(widget.winfo_children() if widget is not self.site_canvas else [])

    def _build_rules_tab(self) -> None:
        left = ttk.Frame(self.rules_tab)
        left.pack(side="left", fill="y", padx=(0, 14))
        self.rules_tree = ttk.Treeview(left, columns=("method",), show="tree headings", height=10)
        self.rules_tree.heading("#0", text="Field")
        self.rules_tree.heading("method", text="Method")
        self.rules_tree.column("#0", width=150)
        self.rules_tree.column("method", width=150)
        self.rules_tree.pack(fill="y")
        for field_name in FIELD_NAMES:
            self.rules_tree.insert("", "end", iid=field_name, text=FIELD_LABELS[field_name], values=("auto",))
        self.rules_tree.bind("<<TreeviewSelect>>", self.load_rule_editor)

        editor = ttk.Frame(self.rules_tab)
        editor.pack(side="left", fill="both", expand=True)
        self.rule_field_var = tk.StringVar(value="post_number")
        self.rule_method_var = tk.StringVar(value="auto")
        self.rule_source_var = tk.StringVar(value="text")
        self.rule_pattern_var = tk.StringVar()
        self.rule_end_var = tk.StringVar()
        self.rule_attribute_var = tk.StringVar()
        self.rule_group_var = tk.StringVar(value="1")
        self.rule_date_format_var = tk.StringVar()
        rows = [
            ("Method", ttk.Combobox(editor, textvariable=self.rule_method_var, values=("auto","css_selector","meta","regex","text_after","text_between","url_regex"), state="readonly")),
            ("Source", ttk.Combobox(editor, textvariable=self.rule_source_var, values=("text","html","url"), state="readonly")),
            ("Pattern / selector / marker", ttk.Entry(editor, textvariable=self.rule_pattern_var)),
            ("End marker", ttk.Entry(editor, textvariable=self.rule_end_var)),
            ("HTML attribute", ttk.Entry(editor, textvariable=self.rule_attribute_var)),
            ("Regex group", ttk.Entry(editor, textvariable=self.rule_group_var)),
            ("Date format (optional)", ttk.Entry(editor, textvariable=self.rule_date_format_var)),
        ]
        for row, (label, widget) in enumerate(rows):
            ttk.Label(editor, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
            widget.grid(row=row, column=1, sticky="ew", pady=5)
        editor.columnconfigure(1, weight=1)
        ttk.Button(editor, text="Save Rule", style="Accent.TButton", command=self.save_rule_editor).grid(row=len(rows), column=1, sticky="e", pady=(12, 8))
        ttk.Label(
            editor,
            text=("Examples: url_regex → /media/(\\d+)/ ; text_after → marker 'Posted:' ; "
                  "css_selector → '.post-date' ; meta → 'property=article:published_time'. "
                  "Auto uses common metadata/HTML patterns and is only a starting point."),
            style="Muted.TLabel",
            wraplength=620,
        ).grid(row=len(rows)+1, column=0, columnspan=2, sticky="w", pady=8)
        self.rules_tree.selection_set("post_number")

    def _build_preview_tab(self) -> None:
        ttk.Label(self.preview_tab, text="Test your extraction rules against one archived page before a full build.", style="Section.TLabel").pack(anchor="w")
        row = ttk.Frame(self.preview_tab)
        row.pack(fill="x", pady=10)
        self.preview_url_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.preview_url_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Test Page", style="Accent.TButton", command=self.start_preview).pack(side="left", padx=(8, 0))
        self.preview_text = tk.Text(self.preview_tab, wrap="word", height=24)
        self.preview_text.pack(fill="both", expand=True)

    def _build_duplicates_tab(self) -> None:
        top = ttk.Frame(self.duplicates_tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Button(top, text="Analyze Duplicates", style="Accent.TButton", command=lambda: self.start_operation("dedupe")).pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh_duplicates).pack(side="left", padx=6)
        ttk.Button(top, text="Merge Group", command=lambda: self._set_selected_duplicate_decision("merge")).pack(side="left", padx=(14, 4))
        ttk.Button(top, text="Keep Separate", command=lambda: self._set_selected_duplicate_decision("separate")).pack(side="left", padx=4)
        ttk.Button(top, text="Use Selected URL", command=self._set_selected_duplicate_canonical).pack(side="left", padx=4)
        ttk.Label(
            self.duplicates_tab,
            text="Exact groups default to Merge. Probable groups remain separate until you approve them. Expand a group to inspect every archived alias URL.",
            style="Muted.TLabel",
            wraplength=900,
        ).pack(anchor="w", pady=(0, 8))
        columns = ("status", "decision", "post", "title", "host", "first")
        self.duplicates_tree = ttk.Treeview(self.duplicates_tab, columns=columns, show="tree headings")
        self.duplicates_tree.heading("#0", text="Group / URL")
        labels = {"status":"Status","decision":"Decision","post":"Post Number","title":"Title","host":"Host","first":"First Capture"}
        widths = {"status":90,"decision":90,"post":100,"title":250,"host":180,"first":110}
        self.duplicates_tree.column("#0", width=170, anchor="w")
        for key in columns:
            self.duplicates_tree.heading(key, text=labels[key])
            self.duplicates_tree.column(key, width=widths[key], anchor="w")
        self.duplicates_tree.pack(fill="both", expand=True)

    def _build_results_tab(self) -> None:
        top = ttk.Frame(self.results_tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Button(top, text="Refresh", command=self.refresh_results).pack(side="left")
        ttk.Button(top, text="Export CSV", command=lambda: self.start_operation("export")).pack(side="left", padx=6)
        columns = ("post", "date", "title", "category", "url", "first")
        self.results_tree = ttk.Treeview(self.results_tab, columns=columns, show="headings")
        labels = {"post":"Post Number","date":"Posted Date","title":"Title","category":"Category","url":"URL","first":"First Capture"}
        widths = {"post":90,"date":110,"title":240,"category":130,"url":340,"first":110}
        for key in columns:
            self.results_tree.heading(key, text=labels[key])
            self.results_tree.column(key, width=widths[key], anchor="w")
        self.results_tree.pack(fill="both", expand=True)

    def apply_performance_preset(self) -> None:
        self.workers_var.set(str(PERFORMANCE_WORKERS))
        self.replay_delay_var.set(str(PERFORMANCE_REPLAY_DELAY))
        self.cdx_workers_var.set(str(PERFORMANCE_CDX_WORKERS))
        self.cdx_delay_var.set(str(PERFORMANCE_CDX_DELAY))
        self.status_var.set("Performance preset applied. Save the project to keep these values.")

    def _config_from_ui(self) -> ProjectConfig:
        if not self.project_var.get().strip():
            raise ValueError("choose a project folder first")
        base = self.config_obj or new_project_config(Path(self.project_var.get()))
        site = base.site.normalized()
        site.name = self.site_name_var.get()
        site.domain = self.domain_var.get()
        site.include_subdomains = self.subdomains_var.get()
        site.content_path = self.path_var.get()
        site.from_year = int(self.from_year_var.get())
        site.to_year = int(self.to_year_var.get())
        site.day_first_dates = self.day_first_var.get()
        network = base.network.normalized()
        network.workers = int(self.workers_var.get())
        network.download_delay = float(self.replay_delay_var.get())
        network.cdx_workers = int(self.cdx_workers_var.get())
        network.cdx_delay = float(self.cdx_delay_var.get())
        fields = tuple(part.strip() for part in self.duplicate_fields_var.get().split(",") if part.strip())
        dedup = DedupConfig(
            cdx_collapse_urlkey=self.collapse_urlkey_var.get(),
            cross_domain_enabled=self.cross_domain_dedupe_var.get(),
            match_fields=fields,
            cross_host_only=True,
            preferred_domain=self.preferred_domain_var.get() or site.domain,
        ).normalized()
        export = ExportConfig(create_xlsx_presentation=self.xlsx_var.get()).normalized()
        return ProjectConfig(Path(self.project_var.get()), site, network, dedup, export).normalized()

    def _load_into_ui(self, config: ProjectConfig) -> None:
        self.config_obj = config.normalized()
        cfg = self.config_obj
        self.project_var.set(str(cfg.output_dir))
        self.site_name_var.set(cfg.site.name)
        self.domain_var.set(cfg.site.domain)
        self.subdomains_var.set(cfg.site.include_subdomains)
        self.path_var.set(cfg.site.content_path)
        self.from_year_var.set(str(cfg.site.from_year))
        self.to_year_var.set(str(cfg.site.to_year))
        self.workers_var.set(str(cfg.network.workers))
        self.replay_delay_var.set(str(cfg.network.download_delay))
        self.cdx_workers_var.set(str(cfg.network.cdx_workers))
        self.cdx_delay_var.set(str(cfg.network.cdx_delay))
        self.day_first_var.set(cfg.site.day_first_dates)
        self.xlsx_var.set(cfg.export.create_xlsx_presentation)
        self.collapse_urlkey_var.set(cfg.dedup.cdx_collapse_urlkey)
        self.cross_domain_dedupe_var.set(cfg.dedup.cross_domain_enabled)
        self.duplicate_fields_var.set(",".join(cfg.dedup.match_fields))
        self.preferred_domain_var.set(cfg.dedup.preferred_domain or cfg.site.domain)
        for field_name in FIELD_NAMES:
            self.rules_tree.set(field_name, "method", cfg.site.rules[field_name].method)
        self.refresh_stats()
        self.refresh_duplicates()
        self.refresh_results()

    def new_project(self) -> None:
        cfg = ProjectWizard(self).show()
        if cfg is None:
            return
        try:
            self._close_database()
            scaffold = create_project_scaffold(cfg)
            self.database = open_database(scaffold.root, recover_abandoned=True)
            self._load_into_ui(cfg)
            self.status_var.set(
                f"Project created: {scaffold.root.name}. Configure extraction rules, preview them, then begin indexing."
            )
        except Exception as exc:
            messagebox.showerror("Archive Builder", str(exc))

    def open_project(self) -> None:
        path = filedialog.askdirectory(title="Open Archive Builder project")
        if not path:
            return
        config_path = Path(path) / CONFIG_NAME
        if not config_path.exists():
            messagebox.showerror("Archive Builder", f"No {CONFIG_NAME} found in that folder.")
            return
        try:
            cfg = load_project_config(config_path)
            self._close_database()
            self.database = open_database(cfg.output_dir, recover_abandoned=True)
            self._load_into_ui(cfg)
            self.status_var.set("Project opened.")
        except Exception as exc:
            self._close_database()
            self.status_var.set("Could not open project.")
            messagebox.showerror("Archive Builder", str(exc))

    def save_project(self) -> None:
        try:
            cfg = self._config_from_ui()
            save_project_config(cfg)
            self.config_obj = cfg
            self.status_var.set("Project saved.")
        except Exception as exc:
            messagebox.showerror("Archive Builder", str(exc))

    def load_rule_editor(self, _event=None) -> None:
        selection = self.rules_tree.selection()
        if not selection:
            return
        field_name = selection[0]
        self.rule_field_var.set(field_name)
        cfg = self.config_obj or (new_project_config(Path(self.project_var.get())) if self.project_var.get() else None)
        if not cfg:
            return
        rule = cfg.site.rules[field_name]
        self.rule_method_var.set(rule.method)
        self.rule_source_var.set(rule.source)
        self.rule_pattern_var.set(rule.pattern)
        self.rule_end_var.set(rule.end_marker)
        self.rule_attribute_var.set(rule.attribute)
        self.rule_group_var.set(str(rule.group))
        self.rule_date_format_var.set(rule.date_format)

    def save_rule_editor(self) -> None:
        try:
            cfg = self._config_from_ui()
            field_name = self.rule_field_var.get()
            cfg.site.rules[field_name] = ExtractionRule(
                method=self.rule_method_var.get(), source=self.rule_source_var.get(), pattern=self.rule_pattern_var.get(),
                end_marker=self.rule_end_var.get(), attribute=self.rule_attribute_var.get(), group=int(self.rule_group_var.get() or 1),
                date_format=self.rule_date_format_var.get(),
            ).normalized()
            save_project_config(cfg)
            self.config_obj = cfg
            self.rules_tree.set(field_name, "method", cfg.site.rules[field_name].method)
            self.status_var.set(f"Saved {FIELD_LABELS[field_name]} extraction rule.")
        except Exception as exc:
            messagebox.showerror("Archive Builder", str(exc))

    def start_operation(self, mode: str) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Archive Builder", "An operation is already running.")
            return
        try:
            cfg = self._config_from_ui()
            save_project_config(cfg)
            self.config_obj = cfg
            if self.database is None:
                self.database = open_database(cfg.output_dir)
        except Exception as exc:
            messagebox.showerror("Archive Builder", str(exc))
            return
        self.stop_event = threading.Event()
        self.progress["value"] = 0
        self.status_var.set(f"Starting {mode}…")
        project_dir = cfg.output_dir

        def run() -> None:
            db = None
            try:
                db = open_database(project_dir)
                result = run_operation(cfg, db, mode, self.stop_event, lambda event: self.events.put_progress(event))
                self.events.put(("done", result))
            except Stopped:
                self.events.put(("stopped", None))
            except Exception as exc:
                self.events.put(("error", exc))
            finally:
                if db is not None:
                    db.close()
        self.worker = threading.Thread(target=run, daemon=True, name=f"archive-builder-{mode}")
        self.worker.start()

    def stop_operation(self) -> None:
        self.stop_event.set()
        self.status_var.set("Stopping after the current safe checkpoint…")

    def start_preview(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Archive Builder", "Wait for the current operation to finish first.")
            return
        try:
            cfg = self._config_from_ui()
            value = self.preview_url_var.get().strip()
            if not value:
                raise ValueError("enter an original URL or Wayback URL to test")
        except Exception as exc:
            messagebox.showerror("Archive Builder", str(exc))
            return
        self.stop_event = threading.Event()
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("end", "Loading archived page…\n")
        def run() -> None:
            try:
                original, timestamp, results = preview_page(cfg, value, self.stop_event)
                self.events.put(("preview", (original, timestamp, results)))
            except Exception as exc:
                self.events.put(("error", exc))
        self.worker = threading.Thread(target=run, daemon=True, name="archive-builder-preview")
        self.worker.start()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress" and isinstance(payload, ProgressEvent):
                    detail = payload.detail or {}
                    rate = detail.get("completed_pages_per_second")
                    starts = detail.get("request_starts_per_second")
                    suffix = ""
                    if rate is not None or starts is not None:
                        suffix = f" | complete {float(rate or 0):.2f}/s | starts {float(starts or 0):.2f}/s"
                    self.status_var.set(payload.message + suffix)
                    if payload.total:
                        self.progress["maximum"] = payload.total
                        self.progress["value"] = payload.current or 0
                elif kind == "done":
                    self.status_var.set("Operation complete.")
                    self.progress["value"] = self.progress["maximum"]
                    self.refresh_stats(); self.refresh_duplicates(); self.refresh_results()
                elif kind == "stopped":
                    self.status_var.set("Operation stopped. Progress has been saved.")
                    self.refresh_stats()
                elif kind == "error":
                    self.status_var.set("Operation failed.")
                    messagebox.showerror("Archive Builder", str(payload))
                    self.refresh_stats()
                elif kind == "preview":
                    original, timestamp, results = payload
                    self.preview_text.delete("1.0", "end")
                    self.preview_text.insert("end", f"URL: {original}\nCapture: {timestamp}\n\n")
                    for field_name in FIELD_NAMES:
                        result = results[field_name]
                        self.preview_text.insert("end", f"{FIELD_LABELS[field_name]}: {result.value or '[not found]'}\n")
                        self.preview_text.insert("end", f"  method={result.method or '-'} confidence={result.confidence or '-'}")
                        if result.error:
                            self.preview_text.insert("end", f" error={result.error}")
                        self.preview_text.insert("end", "\n\n")
                    self.status_var.set("Preview complete.")
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def refresh_stats(self) -> None:
        if self.database is None:
            return
        counts = page_counts(self.database)
        self.stats_var.set(
            f"Indexed: {counts['total']:,} | Downloadable: {counts['downloadable']:,} | "
            f"Downloaded: {counts['downloaded']:,} | Extracted: {counts['extracted']:,} | Errors: {counts['errors']:,}"
        )
        dupes = duplicate_counts(self.database)
        self.duplicate_stats_var.set(
            f"Duplicate groups: {dupes['groups']:,} | Exact: {dupes['exact']:,} | "
            f"Probable: {dupes['probable']:,} | Needs review: {dupes['review']:,}"
        )

    def refresh_duplicates(self) -> None:
        if self.database is None or not hasattr(self, "duplicates_tree"):
            return
        for item in self.duplicates_tree.get_children():
            self.duplicates_tree.delete(item)
        groups = self.database.execute(
            "SELECT * FROM duplicate_groups ORDER BY CASE status WHEN 'probable' THEN 0 ELSE 1 END,id"
        ).fetchall()
        members = self.database.execute(
            """
            SELECT m.group_id,m.page_id,m.is_canonical,p.original_url,p.first_capture_timestamp,
                   MAX(CASE WHEN e.field_name='post_number' THEN e.value END) post_number,
                   MAX(CASE WHEN e.field_name='title' THEN e.value END) title
            FROM duplicate_group_members m
            JOIN pages p ON p.id=m.page_id
            LEFT JOIN extracted_fields e ON e.page_id=p.id
            GROUP BY m.group_id,p.id
            ORDER BY m.group_id,m.is_canonical DESC,p.original_url
            """
        ).fetchall()
        by_group = {}
        for member in members:
            by_group.setdefault(int(member["group_id"]), []).append(member)
        from urllib.parse import urlsplit
        from ..utils import timestamp_to_date
        for group in groups:
            gid = int(group["id"])
            parent = f"group:{gid}"
            self.duplicates_tree.insert("", "end", iid=parent, text=f"Group {gid}", values=(
                group["status"], group["decision"], group["post_number"] or "", group["title"] or "",
                f"{group['member_count']} URLs", timestamp_to_date(group["first_capture_timestamp"], display=True),
            ))
            for member in by_group.get(gid, []):
                page_id = int(member["page_id"])
                host = (urlsplit(member["original_url"]).hostname or "")
                label = "Canonical" if member["is_canonical"] else "Alias"
                self.duplicates_tree.insert(parent, "end", iid=f"page:{gid}:{page_id}", text=label, values=(
                    "", "", member["post_number"] or "", member["title"] or "", host,
                    timestamp_to_date(member["first_capture_timestamp"], display=True),
                ))

    def _selected_duplicate_group(self) -> int | None:
        selection = self.duplicates_tree.selection() if hasattr(self, "duplicates_tree") else ()
        if not selection:
            return None
        token = selection[0]
        parts = token.split(":")
        if parts[0] == "group" and len(parts) == 2:
            return int(parts[1])
        if parts[0] == "page" and len(parts) == 3:
            return int(parts[1])
        return None

    def _set_selected_duplicate_decision(self, decision: str) -> None:
        if self.database is None:
            return
        group_id = self._selected_duplicate_group()
        if group_id is None:
            messagebox.showinfo("Archive Builder", "Select a duplicate group or one of its URLs first.")
            return
        set_duplicate_decision(self.database, group_id, decision)
        self.refresh_duplicates(); self.refresh_stats(); self.refresh_results()
        self.status_var.set(f"Duplicate group {group_id} set to {decision}.")

    def _set_selected_duplicate_canonical(self) -> None:
        if self.database is None:
            return
        selection = self.duplicates_tree.selection() if hasattr(self, "duplicates_tree") else ()
        if not selection or not selection[0].startswith("page:"):
            messagebox.showinfo("Archive Builder", "Select a URL inside a duplicate group first.")
            return
        _, group_id, page_id = selection[0].split(":")
        set_duplicate_canonical(self.database, int(group_id), int(page_id))
        self.refresh_duplicates(); self.refresh_results()
        self.status_var.set(f"Canonical URL changed for duplicate group {group_id}.")

    def refresh_results(self) -> None:
        if self.database is None:
            return
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        from ..extraction.dates import display_date
        from ..utils import timestamp_to_date
        for row in preview_logical_rows(self.database, limit=500):
            self.results_tree.insert("", "end", values=(
                row.get("post_number") or "", display_date(str(row.get("posted_date") or "")),
                row.get("title") or "", row.get("category") or "", row.get("original_url") or "",
                timestamp_to_date(str(row.get("first_capture_timestamp") or ""), display=True),
            ))

    def _close_database(self) -> None:
        if self.database is not None:
            self.database.close()
            self.database = None

    def destroy(self) -> None:
        self.stop_event.set()
        self._close_database()
        super().destroy()
