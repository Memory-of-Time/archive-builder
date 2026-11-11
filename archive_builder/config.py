from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .constants import CONFIG_NAME, DEFAULT_USER_AGENT, FIELD_NAMES
from .utils import atomic_write_text, normalized_content_path, stable_json_hash


RULE_METHODS = (
    "auto",
    "css_selector",
    "meta",
    "regex",
    "text_after",
    "text_between",
    "url_regex",
)
RULE_SOURCES = ("text", "html", "url")

# Archive Scout v1.0.5-style acquisition preset. Saved projects keep their explicit
# values; the UI exposes this as an opt-in preset for older projects.
PERFORMANCE_WORKERS = 10
PERFORMANCE_REPLAY_DELAY = 0.125
PERFORMANCE_CDX_WORKERS = 10
PERFORMANCE_CDX_DELAY = 0.75


@dataclass(slots=True)
class ExtractionRule:
    method: str = "auto"
    source: str = "text"
    pattern: str = ""
    end_marker: str = ""
    attribute: str = ""
    group: int = 1
    date_format: str = ""

    def normalized(self) -> "ExtractionRule":
        method = (self.method or "auto").strip().casefold()
        if method not in RULE_METHODS:
            raise ValueError(f"unsupported extraction method: {method}")
        source = (self.source or "text").strip().casefold()
        if source not in RULE_SOURCES:
            source = "text"
        return ExtractionRule(
            method=method,
            source=source,
            pattern=(self.pattern or "").strip(),
            end_marker=(self.end_marker or "").strip(),
            attribute=(self.attribute or "").strip(),
            group=max(0, int(self.group)),
            date_format=(self.date_format or "").strip(),
        )


@dataclass(slots=True)
class NetworkConfig:
    workers: int = PERFORMANCE_WORKERS
    cdx_workers: int = PERFORMANCE_CDX_WORKERS
    cdx_page_size: int = 5000
    cdx_delay: float = PERFORMANCE_CDX_DELAY
    download_delay: float = PERFORMANCE_REPLAY_DELAY
    retries: int = 4
    retry_base_seconds: float = 5.0
    retry_max_seconds: float = 120.0
    connect_timeout: float = 30.0
    read_timeout: float = 120.0
    max_html_mb: float = 20.0
    user_agent: str = DEFAULT_USER_AGENT

    def normalized(self) -> "NetworkConfig":
        return NetworkConfig(
            workers=min(32, max(1, int(self.workers))),
            cdx_workers=min(16, max(1, int(self.cdx_workers))),
            cdx_page_size=min(100000, max(100, int(self.cdx_page_size))),
            cdx_delay=max(0.0, float(self.cdx_delay)),
            download_delay=max(0.0, float(self.download_delay)),
            retries=min(12, max(1, int(self.retries))),
            retry_base_seconds=max(0.5, float(self.retry_base_seconds)),
            retry_max_seconds=max(float(self.retry_base_seconds), float(self.retry_max_seconds)),
            connect_timeout=max(5.0, float(self.connect_timeout)),
            read_timeout=max(10.0, float(self.read_timeout)),
            max_html_mb=max(0.5, float(self.max_html_mb)),
            user_agent=(self.user_agent or DEFAULT_USER_AGENT).strip(),
        )

    @property
    def max_html_bytes(self) -> int:
        return int(self.max_html_mb * 1024 * 1024)

    @classmethod
    def performance_preset(cls) -> "NetworkConfig":
        return cls(
            workers=PERFORMANCE_WORKERS,
            cdx_workers=PERFORMANCE_CDX_WORKERS,
            cdx_delay=PERFORMANCE_CDX_DELAY,
            download_delay=PERFORMANCE_REPLAY_DELAY,
        )


@dataclass(slots=True)
class SiteProfile:
    name: str = "NothingToxic"
    domain: str = "nothingtoxic.com"
    include_subdomains: bool = True
    content_path: str = "/media/"
    from_year: int = 1996
    to_year: int = field(default_factory=lambda: datetime.now().year)
    # Used only when an extraction rule does not provide an explicit date_format.
    # This makes ambiguous numeric dates a site decision rather than a display side effect.
    day_first_dates: bool = False
    rules: dict[str, ExtractionRule | dict] = field(default_factory=dict)

    def normalized(self) -> "SiteProfile":
        domain = (self.domain or "").strip().casefold()
        for prefix in ("https://", "http://"):
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
        domain = domain.strip("/.* ")
        if not domain or "." not in domain:
            raise ValueError("enter a valid domain, such as nothingtoxic.com")
        start = max(1996, int(self.from_year))
        end = min(2100, int(self.to_year))
        if start > end:
            raise ValueError("From year cannot be after To year")
        normalized_rules: dict[str, ExtractionRule] = {}
        for field_name in FIELD_NAMES:
            raw = self.rules.get(field_name, ExtractionRule())
            if isinstance(raw, dict):
                raw = ExtractionRule(**raw)
            normalized_rules[field_name] = raw.normalized()
        return SiteProfile(
            name=(self.name or domain).strip(),
            domain=domain,
            include_subdomains=bool(self.include_subdomains),
            content_path=normalized_content_path(self.content_path),
            from_year=start,
            to_year=end,
            day_first_dates=bool(self.day_first_dates),
            rules=normalized_rules,
        )

    def extraction_fingerprint(self) -> str:
        profile = self.normalized()
        return stable_json_hash({
            "content_path": profile.content_path,
            "day_first_dates": profile.day_first_dates,
            "rules": {name: asdict(rule) for name, rule in profile.rules.items()},
        })


@dataclass(slots=True)
class DedupConfig:
    cdx_collapse_urlkey: bool = True
    cross_domain_enabled: bool = True
    match_fields: tuple[str, ...] | list[str] = ("post_number", "title")
    cross_host_only: bool = True
    preferred_domain: str = ""

    def normalized(self) -> "DedupConfig":
        fields: list[str] = []
        for raw in self.match_fields or ():
            name = str(raw).strip().casefold()
            if name in FIELD_NAMES and name not in fields:
                fields.append(name)
        if not fields:
            fields = ["post_number", "title"]
        preferred = (self.preferred_domain or "").strip().casefold()
        for prefix in ("https://", "http://"):
            if preferred.startswith(prefix):
                preferred = preferred[len(prefix):]
        preferred = preferred.strip("/.* ")
        return DedupConfig(
            cdx_collapse_urlkey=bool(self.cdx_collapse_urlkey),
            cross_domain_enabled=bool(self.cross_domain_enabled),
            match_fields=tuple(fields),
            cross_host_only=bool(self.cross_host_only),
            preferred_domain=preferred,
        )


@dataclass(slots=True)
class ExportConfig:
    create_xlsx_presentation: bool = False
    presentation_max_rows: int = 50000

    def normalized(self) -> "ExportConfig":
        return ExportConfig(
            create_xlsx_presentation=bool(self.create_xlsx_presentation),
            presentation_max_rows=min(250000, max(1, int(self.presentation_max_rows))),
        )


@dataclass(slots=True)
class ProjectConfig:
    output_dir: Path
    site: SiteProfile | dict = field(default_factory=SiteProfile)
    network: NetworkConfig | dict = field(default_factory=NetworkConfig)
    dedup: DedupConfig | dict = field(default_factory=DedupConfig)
    export: ExportConfig | dict = field(default_factory=ExportConfig)

    def normalized(self) -> "ProjectConfig":
        site = self.site if isinstance(self.site, SiteProfile) else SiteProfile(**self.site)
        network = self.network if isinstance(self.network, NetworkConfig) else NetworkConfig(**self.network)
        dedup = self.dedup if isinstance(self.dedup, DedupConfig) else DedupConfig(**self.dedup)
        export = self.export if isinstance(self.export, ExportConfig) else ExportConfig(**self.export)
        normalized_site = site.normalized()
        normalized_dedup = dedup.normalized()
        if not normalized_dedup.preferred_domain:
            normalized_dedup.preferred_domain = normalized_site.domain
        return ProjectConfig(
            output_dir=Path(self.output_dir).expanduser().resolve(),
            site=normalized_site,
            network=network.normalized(),
            dedup=normalized_dedup,
            export=export.normalized(),
        )

    def to_payload(self) -> dict:
        cfg = self.normalized()
        return {
            "version": 3,
            "output_dir": str(cfg.output_dir),
            "site": {
                **{k: v for k, v in asdict(cfg.site).items() if k != "rules"},
                "rules": {name: asdict(rule) for name, rule in cfg.site.rules.items()},
            },
            "network": asdict(cfg.network),
            "dedup": asdict(cfg.dedup),
            "export": asdict(cfg.export),
        }


def nothingtoxic_profile() -> SiteProfile:
    return SiteProfile(
        name="NothingToxic",
        domain="nothingtoxic.com",
        include_subdomains=True,
        content_path="/media/",
        from_year=1996,
        to_year=datetime.now().year,
        rules={
            "post_number": ExtractionRule(method="auto", source="url"),
            "posted_date": ExtractionRule(method="auto", source="text"),
            "title": ExtractionRule(method="auto", source="text"),
            "category": ExtractionRule(method="auto", source="text"),
            "description": ExtractionRule(method="auto", source="text"),
        },
    )


def new_project_config(output_dir: Path) -> ProjectConfig:
    return ProjectConfig(output_dir=Path(output_dir), site=nothingtoxic_profile()).normalized()


def save_project_config(config: ProjectConfig) -> Path:
    cfg = config.normalized()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.output_dir / CONFIG_NAME
    atomic_write_text(path, json.dumps(cfg.to_payload(), indent=2, ensure_ascii=False) + "\n")
    return path


def load_project_config(path: Path) -> ProjectConfig:
    path = Path(path)
    if path.is_dir():
        path = path / CONFIG_NAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    site_raw = dict(payload.get("site") or {})
    rules_raw = dict(site_raw.pop("rules", {}) or {})
    site_raw["rules"] = {name: ExtractionRule(**value) for name, value in rules_raw.items()}
    network_raw = dict(payload.get("network") or {})
    # Saved v0.1.2 projects intentionally retain workers=4 / delay=0.5 until the
    # user applies the performance preset. Missing values use the new defaults.
    return ProjectConfig(
        output_dir=Path(payload.get("output_dir") or path.parent),
        site=SiteProfile(**site_raw),
        network=NetworkConfig(**network_raw),
        dedup=DedupConfig(**dict(payload.get("dedup") or {})),
        export=ExportConfig(**dict(payload.get("export") or {})),
    ).normalized()
