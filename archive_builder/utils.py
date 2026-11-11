from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def stable_json_hash(payload: object, length: int = 20) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename_for_url(url: str, timestamp: str | None = None) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "page").replace(".", "_")
    identity = f"{timestamp or ''}\n{url}"
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
    suffix = f"_{str(timestamp)[:14]}" if timestamp else ""
    return f"{host}{suffix}_{digest}.html"


def timestamp_to_date(timestamp: str | None, *, display: bool = False) -> str:
    value = str(timestamp or "")
    if len(value) >= 8 and value[:8].isdigit():
        if display:
            return f"{value[6:8]}/{value[4:6]}/{value[:4]}"
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return ""


def normalized_content_path(value: str) -> str:
    value = (value or "/").strip()
    while value.endswith("*"):
        value = value[:-1].rstrip()
    if not value.startswith("/"):
        value = "/" + value
    if not value.endswith("/"):
        value += "/"
    return value
