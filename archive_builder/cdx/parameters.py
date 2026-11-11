from __future__ import annotations

import re

from ..config import ProjectConfig
from ..utils import stable_json_hash


def query_signature(config: ProjectConfig, phase: str) -> str:
    cfg = config.normalized()
    return stable_json_hash({
        "domain": cfg.site.domain,
        "include_subdomains": cfg.site.include_subdomains,
        "content_path": cfg.site.content_path,
        "from_year": cfg.site.from_year,
        "to_year": cfg.site.to_year,
        "phase": phase,
        "collapse_urlkey": cfg.dedup.cdx_collapse_urlkey,
        "fields": "urlkey,timestamp,original,mimetype,statuscode,digest,length",
    })


def _range_value(value: int | str, *, end: bool = False) -> str:
    if isinstance(value, int):
        return f"{value:04d}{'1231235959' if end else '0101000000'}"
    text = str(value).strip()
    if re.fullmatch(r"\d{4}", text):
        return f"{text}{'1231235959' if end else '0101000000'}"
    if re.fullmatch(r"\d{14}", text):
        return text
    raise ValueError(f"invalid CDX range value: {value!r}")


def _domain_path_filter(domain: str, path: str) -> str:
    domain_re = re.escape(domain)
    path_re = re.escape(path)
    # Anchor to the actual URL path so a query-string occurrence of /media/ is not
    # sufficient. The local prepared scope check remains authoritative too.
    return rf"^https?://(?:[^/]+\.)?{domain_re}{path_re}"


def build_params(
    config: ProjectConfig,
    phase: str,
    start: int | str,
    end: int | str | None = None,
    resume_key: str | None = None,
    *,
    page: int | None = None,
    page_size: int | None = None,
) -> list[tuple[str, str]]:
    """Build one CDX query without manually encoding query values.

    ``start`` may be a year for backwards compatibility. If ``end`` is omitted and
    start is a year, the request covers that year. Runtime indexing passes the full
    requested interval instead of probing every year unconditionally.
    """
    cfg = config.normalized()
    if end is None:
        end = start
    start_value = _range_value(start, end=False)
    end_value = _range_value(end, end=True)

    params: list[tuple[str, str]] = []
    if cfg.site.include_subdomains:
        params.extend([
            ("url", cfg.site.domain),
            ("matchType", "domain"),
            ("filter", f"original:{_domain_path_filter(cfg.site.domain, cfg.site.content_path)}"),
        ])
    else:
        # Prefix search is both narrower and cheaper when a single host is requested.
        params.extend([
            ("url", f"{cfg.site.domain}{cfg.site.content_path}*"),
            ("matchType", "prefix"),
        ])

    params.extend([
        ("from", start_value),
        ("to", end_value),
        ("output", "json"),
        ("fl", "urlkey,timestamp,original,mimetype,statuscode,digest,length"),
    ])
    if cfg.dedup.cdx_collapse_urlkey:
        params.append(("collapse", "urlkey"))
    if phase == "representative":
        # Actual replay bytes decide whether the candidate is usable text. Restricting
        # this phase to text/html loses XHTML, revisit and mislabeled pages.
        params.append(("filter", "statuscode:200"))

    if page is None:
        params.extend([
            ("limit", str(cfg.network.cdx_page_size)),
            ("showResumeKey", "true"),
        ])
        if resume_key:
            params.append(("resumeKey", resume_key))
    else:
        params.append(("page", str(max(0, int(page)))))
        params.append(("pageSize", str(max(1, min(50, int(page_size or 9))))))
    return params
