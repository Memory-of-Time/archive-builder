from __future__ import annotations

import re
import threading
from urllib.parse import unquote

from ..cdx.client import WaybackClient, WaybackError
from ..config import ProjectConfig
from ..extraction import compile_extraction_plan, extract_fields
from .downloader import (
    _reject_wayback_error_html,
    _validate_wayback_final_url,
    classify_replay,
    decode_document,
    replay_url,
)

WAYBACK_RE = re.compile(r"https?://web\.archive\.org/web/(\d{4,14})(?:[a-z_]+)?/(https?://.+)", re.I)


def parse_preview_url(value: str) -> tuple[str, str | None]:
    value = value.strip()
    match = WAYBACK_RE.match(value)
    if match:
        return unquote(match.group(2)), match.group(1)
    return value, None


def preview_page(config: ProjectConfig, value: str, stop_event: threading.Event) -> tuple[str, str, dict]:
    cfg = config.normalized()
    # Rule failures should surface before any network request is made.
    plan = compile_extraction_plan(cfg.site)
    original_url, timestamp = parse_preview_url(value)
    client = WaybackClient(cfg.network, stop_event, delay=cfg.network.download_delay)
    try:
        if not timestamp:
            params = [
                ("url", original_url),
                ("output", "json"),
                ("fl", "urlkey,timestamp,original,mimetype,statuscode,digest,length"),
                ("filter", "statuscode:200"),
                ("limit", "1"),
            ]
            result = client.cdx(params)
            if not result.rows:
                raise WaybackError("no successful archived capture was found for that URL")
            timestamp = result.rows[0]["timestamp"]
        replay = client.stream_bytes(replay_url(timestamp, original_url), cfg.network.max_html_bytes)
        _validate_wayback_final_url(replay.final_url)
        classification, reason = classify_replay(replay.data, replay.content_type)
        if classification == "known_binary":
            raise WaybackError(f"selected capture is binary rather than an extractable page ({reason})")
        text, _ = decode_document(replay.data, replay.content_type)
        _reject_wayback_error_html(text)
        return original_url, timestamp, extract_fields(text, original_url, cfg.site, plan=plan)
    finally:
        client.close()
