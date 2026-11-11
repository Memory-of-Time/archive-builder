from __future__ import annotations

import email.utils
import hashlib
import json
import random
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from ..config import NetworkConfig
from ..constants import CDX_URL
from ..events import Stopped


class WaybackError(RuntimeError):
    pass


class RateLimitDeferred(WaybackError):
    pass


class PermanentWaybackError(WaybackError):
    """A response that retrying the same request is not expected to repair."""
    pass


@dataclass(slots=True)
class CDXRows:
    rows: list[dict[str, str]]
    resume_key: str | None
    response_fingerprint: str = ""


@dataclass(slots=True)
class ReplayBytes:
    data: bytes
    status_code: int
    final_url: str
    content_type: str
    headers: dict[str, str]


class SharedRateGate:
    def __init__(self, delay: float) -> None:
        self.delay = max(0.0, float(delay))
        self.condition = threading.Condition()
        self.next_request = 0.0
        self.blocked_until = 0.0
        self.starts = 0
        self.first_start = 0.0
        self.last_start = 0.0

    def wait(self, stop_event: threading.Event) -> None:
        while True:
            with self.condition:
                if stop_event.is_set():
                    raise Stopped
                now = time.monotonic()
                wait = max(self.next_request, self.blocked_until) - now
                if wait <= 0:
                    self.next_request = now + self.delay
                    self.starts += 1
                    if not self.first_start:
                        self.first_start = now
                    self.last_start = now
                    return
                self.condition.wait(timeout=min(max(wait, 0.02), 0.25))

    def pause(self, seconds: float) -> None:
        with self.condition:
            self.blocked_until = max(self.blocked_until, time.monotonic() + max(1.0, seconds))
            self.condition.notify_all()

    @property
    def starts_per_second(self) -> float:
        if self.starts <= 1 or self.last_start <= self.first_start:
            return 0.0
        return (self.starts - 1) / (self.last_start - self.first_start)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def _interruptible_wait(stop_event: threading.Event, seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if stop_event.is_set():
            raise Stopped
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        stop_event.wait(min(0.25, remaining))


def _valid_original_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except Exception:
        return False
    return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.netloc)


def parse_cdx(payload: object) -> CDXRows:
    if payload is None:
        raise WaybackError("CDX returned JSON null instead of a complete result")
    if not isinstance(payload, list) or not payload:
        raise WaybackError("unexpected or incomplete CDX response")
    header = payload[0]
    if not isinstance(header, list) or not header or not all(isinstance(item, str) for item in header):
        raise WaybackError("unexpected CDX response header")
    if len(set(header)) != len(header) or "timestamp" not in header or "original" not in header:
        raise WaybackError("CDX response is missing required timestamp/original fields")

    body = list(payload[1:])
    resume = None
    if body and body[-1] == []:
        raise WaybackError("truncated CDX continuation framing")
    if len(body) >= 2 and body[-2] == []:
        token_row = body[-1]
        if not isinstance(token_row, list) or len(token_row) != 1 or not str(token_row[0]):
            raise WaybackError("invalid CDX continuation token")
        resume = str(token_row[0])
        body = body[:-2]

    rows: list[dict[str, str]] = []
    for index, item in enumerate(body, start=1):
        if not isinstance(item, list) or len(item) != len(header):
            raise WaybackError(f"malformed CDX row {index}: wrong field count")
        row = {key: str(value or "") for key, value in zip(header, item)}
        timestamp = row.get("timestamp", "")
        original = row.get("original", "")
        if not re.fullmatch(r"\d{14}", timestamp):
            raise WaybackError(f"malformed CDX row {index}: invalid timestamp")
        if not _valid_original_url(original):
            raise WaybackError(f"malformed CDX row {index}: invalid original URL")
        rows.append(row)

    fingerprint_source = json.dumps([header, *body, resume], ensure_ascii=False, separators=(",", ":"))
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
    return CDXRows(rows, resume, fingerprint)


class WaybackClient:
    def __init__(
        self,
        network: NetworkConfig,
        stop_event: threading.Event,
        delay: float | None = None,
        *,
        shared_gate: SharedRateGate | None = None,
    ) -> None:
        self.network = network.normalized()
        self.stop_event = stop_event
        self.gate = shared_gate or SharedRateGate(self.network.cdx_delay if delay is None else delay)
        self.client = httpx.Client(
            headers={"User-Agent": self.network.user_agent, "Accept-Language": "en-US,en;q=0.8"},
            follow_redirects=True,
            timeout=httpx.Timeout(self.network.read_timeout, connect=self.network.connect_timeout),
        )

    def close(self) -> None:
        self.client.close()

    def _backoff(self, attempt: int) -> float:
        return min(
            self.network.retry_max_seconds,
            self.network.retry_base_seconds * (2 ** min(attempt - 1, 5)),
        ) * random.uniform(0.9, 1.1)

    def _request(self, url: str, *, params=None, accept: str = "*/*") -> httpx.Response:
        last_error: Exception | None = None
        rate_limited = False
        for attempt in range(1, self.network.retries + 1):
            if self.stop_event.is_set():
                raise Stopped
            self.gate.wait(self.stop_event)
            try:
                response = self.client.get(url, params=params, headers={"Accept": accept})
                if response.status_code in {429, 503}:
                    rate_limited = True
                    retry_after = parse_retry_after(response.headers.get("retry-after"))
                    # A shared archive throttle should never be shorter than the configured
                    # base retry interval.  Backoff jitter may reduce an individual wait,
                    # but rate-limit responses (429/503) are a service-wide signal and must
                    # defer every worker by at least retry_base_seconds unless Retry-After
                    # explicitly asks for longer.
                    if retry_after is not None:
                        pause = max(self.network.retry_base_seconds, retry_after)
                    else:
                        pause = max(self.network.retry_base_seconds, self._backoff(attempt))
                    self.gate.pause(pause)
                    last_error = WaybackError(f"HTTP {response.status_code}")
                    if attempt < self.network.retries:
                        continue
                    break
                if response.status_code >= 500:
                    last_error = WaybackError(f"HTTP {response.status_code}")
                    if attempt < self.network.retries:
                        _interruptible_wait(self.stop_event, self._backoff(attempt))
                        continue
                    break
                if 400 <= response.status_code < 500:
                    raise PermanentWaybackError(f"HTTP {response.status_code}")
                response.raise_for_status()
                return response
            except Stopped:
                raise
            except PermanentWaybackError:
                raise
            except (httpx.HTTPError, OSError, TimeoutError, WaybackError) as exc:
                last_error = exc
                if attempt >= self.network.retries:
                    break
                _interruptible_wait(self.stop_event, self._backoff(attempt))
        if rate_limited:
            raise RateLimitDeferred(f"Wayback rate limit persisted after bounded retries: {last_error}")
        raise WaybackError(str(last_error or "Wayback request failed"))

    def cdx(self, params: list[tuple[str, str]], max_bytes: int = 64 * 1024 * 1024) -> CDXRows:
        response = self._request(CDX_URL, params=params, accept="application/json,text/plain,*/*")
        if len(response.content) > max_bytes:
            raise WaybackError(f"CDX response exceeds {max_bytes:,} bytes")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            preview = response.text[:300].replace("\n", " ")
            raise WaybackError(f"malformed CDX JSON response: {preview}") from exc
        return parse_cdx(payload)

    def num_pages(self, params: list[tuple[str, str]], page_size: int = 9) -> int:
        base = [(k, v) for k, v in params if k not in {"output", "limit", "showResumeKey", "resumeKey", "page", "pageSize", "showNumPages"}]
        base.extend([("showNumPages", "true"), ("pageSize", str(max(1, min(50, int(page_size)))))])
        response = self._request(CDX_URL, params=base, accept="text/plain,*/*")
        text = response.text.strip()
        if not re.fullmatch(r"\d+", text):
            raise WaybackError(f"invalid CDX page-count response: {text[:120]!r}")
        return int(text)

    def stream_bytes(self, url: str, max_bytes: int, *, accept: str = "text/html,application/xhtml+xml,text/plain,*/*;q=0.4") -> ReplayBytes:
        last_error: Exception | None = None
        rate_limited = False
        for attempt in range(1, self.network.retries + 1):
            if self.stop_event.is_set():
                raise Stopped
            self.gate.wait(self.stop_event)
            try:
                with self.client.stream("GET", url, headers={"Accept": accept}) as response:
                    if response.status_code in {429, 503}:
                        rate_limited = True
                        retry_after = parse_retry_after(response.headers.get("retry-after"))
                        pause = retry_after if retry_after is not None else self._backoff(attempt)
                        self.gate.pause(pause)
                        last_error = WaybackError(f"HTTP {response.status_code}")
                        if attempt < self.network.retries:
                            continue
                        break
                    if response.status_code >= 500:
                        last_error = WaybackError(f"HTTP {response.status_code}")
                        if attempt < self.network.retries:
                            _interruptible_wait(self.stop_event, self._backoff(attempt))
                            continue
                        break
                    if 400 <= response.status_code < 500:
                        raise PermanentWaybackError(f"HTTP {response.status_code}")
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        if self.stop_event.is_set():
                            raise Stopped
                        total += len(chunk)
                        if total > max_bytes:
                            raise WaybackError(f"response exceeds {max_bytes:,} bytes")
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    return ReplayBytes(
                        data=data,
                        status_code=int(response.status_code),
                        final_url=str(response.url),
                        content_type=str(response.headers.get("content-type") or ""),
                        headers={str(k).casefold(): str(v) for k, v in response.headers.items()},
                    )
            except Stopped:
                raise
            except PermanentWaybackError:
                raise
            except (httpx.HTTPError, OSError, TimeoutError, WaybackError) as exc:
                last_error = exc
                if attempt >= self.network.retries:
                    break
                _interruptible_wait(self.stop_event, self._backoff(attempt))
        if rate_limited:
            raise RateLimitDeferred(f"Wayback rate limit persisted after bounded retries: {last_error}")
        raise WaybackError(str(last_error or "Wayback request failed"))

    def get_bytes(self, url: str, max_bytes: int) -> tuple[bytes, int, str]:
        result = self.stream_bytes(url, max_bytes)
        return result.data, result.status_code, result.final_url
