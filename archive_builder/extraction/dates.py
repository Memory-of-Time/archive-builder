from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from dateutil import parser as date_parser


@dataclass(frozen=True, slots=True)
class NormalizedDate:
    value: str = ""
    precision: str = ""
    raw: str = ""


_YEAR_RE = re.compile(r"^\s*(19\d{2}|20\d{2}|2100)\s*$")
_MONTH_YEAR_RE = re.compile(
    r"^\s*(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(19\d{2}|20\d{2}|2100)\s*$",
    re.I,
)
_YEAR_MONTH_RE = re.compile(r"^\s*(19\d{2}|20\d{2}|2100)[-/](0?[1-9]|1[0-2])\s*$")
_FULL_ISO_RE = re.compile(r"^\s*(19\d{2}|20\d{2}|2100)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?:[T\s].*)?$")
_NUMERIC_FULL_RE = re.compile(r"^\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})\s*$")
_YEAR_TOKEN_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2}|2100)(?!\d)")
_DAY_TOKEN_RE = re.compile(r"(?<!\d)([0-3]?\d)(?:st|nd|rd|th)?(?!\d)", re.I)
_MONTH_NAME_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\b",
    re.I,
)


def parse_date(value: str, date_format: str = "", *, day_first: bool = False) -> NormalizedDate:
    raw = (value or "").strip()
    if not raw:
        return NormalizedDate(raw=raw)

    if date_format:
        try:
            parsed = datetime.strptime(raw, date_format)
        except ValueError:
            return NormalizedDate(raw=raw)
        if not (1900 <= parsed.year <= 2100):
            return NormalizedDate(raw=raw)
        return NormalizedDate(parsed.date().isoformat(), "day", raw)

    match = _YEAR_RE.match(raw)
    if match:
        return NormalizedDate(match.group(1), "year", raw)

    match = _MONTH_YEAR_RE.match(raw)
    if match:
        try:
            parsed = date_parser.parse(raw, fuzzy=False, default=datetime(2000, 1, 1))
        except (ValueError, OverflowError, TypeError):
            return NormalizedDate(raw=raw)
        return NormalizedDate(f"{parsed.year:04d}-{parsed.month:02d}", "month", raw)

    match = _YEAR_MONTH_RE.match(raw)
    if match:
        return NormalizedDate(f"{int(match.group(1)):04d}-{int(match.group(2)):02d}", "month", raw)

    match = _FULL_ISO_RE.match(raw)
    if match:
        try:
            parsed = date_parser.isoparse(raw)
        except (ValueError, OverflowError, TypeError):
            try:
                parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
            except ValueError:
                return NormalizedDate(raw=raw)
        return NormalizedDate(parsed.date().isoformat(), "day", raw)

    match = _NUMERIC_FULL_RE.match(raw)
    if match:
        a, b, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        month, day = (b, a) if day_first else (a, b)
        try:
            parsed = datetime(year, month, day)
        except ValueError:
            return NormalizedDate(raw=raw)
        return NormalizedDate(parsed.date().isoformat(), "day", raw)

    # Natural-language dates are only accepted as exact days if the source itself
    # contains both an explicit year and evidence of a day. This avoids dateutil's
    # historical 1900/first-of-month fabrication for partial dates.
    if not _YEAR_TOKEN_RE.search(raw):
        return NormalizedDate(raw=raw)
    if _MONTH_NAME_RE.search(raw):
        # Ignore the digits that belong to the four-digit year when looking for a day.
        without_year = _YEAR_TOKEN_RE.sub("", raw)
        if not _DAY_TOKEN_RE.search(without_year):
            # A month/year phrase that escaped the strict matcher above.
            try:
                parsed = date_parser.parse(raw, fuzzy=True, dayfirst=day_first, default=datetime(2000, 1, 1))
            except (ValueError, OverflowError, TypeError):
                return NormalizedDate(raw=raw)
            return NormalizedDate(f"{parsed.year:04d}-{parsed.month:02d}", "month", raw)

    try:
        parsed = date_parser.parse(raw, fuzzy=True, dayfirst=day_first, default=datetime(2000, 1, 1))
    except (ValueError, OverflowError, TypeError):
        return NormalizedDate(raw=raw)
    if not (1900 <= parsed.year <= 2100):
        return NormalizedDate(raw=raw)
    return NormalizedDate(parsed.date().isoformat(), "day", raw)


def normalize_date(value: str, date_format: str = "", *, day_first: bool = False) -> str:
    return parse_date(value, date_format, day_first=day_first).value


def date_sort_key(value: str, precision: str = "") -> tuple[int, int, int, int]:
    """Return a chronological key without fabricating a displayed exact day.

    Partial dates sort at the start of their represented period. Precision is included
    as a deterministic final component so exact dates sort ahead of less precise evidence
    on the same effective date when ordering newest-first.
    """
    text = (value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        y, m, d = map(int, text.split("-"))
        return y, m, d, 3
    if re.fullmatch(r"\d{4}-\d{2}", text):
        y, m = map(int, text.split("-"))
        return y, m, 0, 2
    if re.fullmatch(r"\d{4}", text):
        return int(text), 0, 0, 1
    return 0, 0, 0, 0


def display_date(value: str) -> str:
    text = (value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        y, m, d = text.split("-")
        return f"{d}/{m}/{y}"
    if re.fullmatch(r"\d{4}-\d{2}", text):
        y, m = text.split("-")
        return f"{m}/{y}"
    if re.fullmatch(r"\d{4}", text):
        return text
    return ""
