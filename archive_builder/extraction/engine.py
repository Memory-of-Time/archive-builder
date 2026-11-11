from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import soupsieve
from bs4 import BeautifulSoup

from ..config import ExtractionRule, SiteProfile
from ..constants import FIELD_NAMES
from ..utils import clean_space
from .dates import parse_date


@dataclass(slots=True)
class ExtractionResult:
    value: str = ""
    method: str = ""
    confidence: str = ""
    error: str = ""
    raw_value: str = ""
    precision: str = ""


@dataclass(frozen=True, slots=True)
class CompiledRule:
    field_name: str
    rule: ExtractionRule
    regex: re.Pattern[str] | None = None


@dataclass(frozen=True, slots=True)
class ExtractionPlan:
    profile: SiteProfile
    rules: dict[str, CompiledRule]
    needs_dom: bool
    needs_text: bool
    needs_html: bool


@dataclass(slots=True)
class PageContext:
    html: str
    url: str
    plan: ExtractionPlan
    _soup: BeautifulSoup | None = None
    _text: str | None = None
    _json_ld: list[Any] | None = None

    @property
    def soup(self) -> BeautifulSoup:
        if self._soup is None:
            self._soup = BeautifulSoup(self.html, "html.parser")
        return self._soup

    @property
    def text(self) -> str:
        if self._text is None:
            self._text = self.soup.get_text("\n", strip=True)
        return self._text

    @property
    def json_ld(self) -> list[Any]:
        if self._json_ld is None:
            values: list[Any] = []
            for script in self.soup.find_all("script", attrs={"type": "application/ld+json"}):
                raw = script.string or script.get_text() or ""
                try:
                    values.append(json.loads(raw))
                except Exception:
                    continue
            self._json_ld = values
        return self._json_ld


def compile_extraction_plan(profile: SiteProfile) -> ExtractionPlan:
    normalized = profile.normalized()
    compiled: dict[str, CompiledRule] = {}
    needs_dom = False
    needs_text = False
    needs_html = False
    for field_name in FIELD_NAMES:
        rule = normalized.rules[field_name]
        regex = None
        if rule.method in {"regex", "url_regex"}:
            if not rule.pattern:
                raise ValueError(f"{field_name}: regex pattern is blank")
            try:
                regex = re.compile(rule.pattern, re.I | re.S)
            except re.error as exc:
                raise ValueError(f"{field_name}: invalid regex: {exc}") from exc
        if rule.method == "css_selector":
            if not rule.pattern:
                raise ValueError(f"{field_name}: CSS selector is blank")
            try:
                soupsieve.compile(rule.pattern)
            except Exception as exc:
                raise ValueError(f"{field_name}: invalid CSS selector: {exc}") from exc
        if rule.method == "meta" and not rule.pattern:
            raise ValueError(f"{field_name}: meta lookup is blank")
        if rule.method in {"text_after", "text_between"} and not rule.pattern:
            raise ValueError(f"{field_name}: text marker is blank")
        if rule.method == "text_between" and not rule.end_marker:
            raise ValueError(f"{field_name}: text_between requires an end marker")

        # Automatic post number is URL-only; other automatic fields require a DOM.
        if rule.method == "auto" and field_name != "post_number":
            needs_dom = True
        elif rule.method in {"css_selector", "meta"}:
            needs_dom = True
        elif rule.method in {"text_after", "text_between", "regex"}:
            if rule.source == "text":
                needs_dom = True
                needs_text = True
            elif rule.source == "html":
                needs_html = True
        compiled[field_name] = CompiledRule(field_name, rule, regex)
    return ExtractionPlan(normalized, compiled, needs_dom, needs_text, needs_html)


def _source_value(rule: ExtractionRule, context: PageContext) -> str:
    if rule.source == "html":
        return context.html
    if rule.source == "url":
        return context.url
    return context.text


def _regex_extract(value: str, compiled: re.Pattern[str], group: int) -> str:
    match = compiled.search(value)
    if not match:
        return ""
    try:
        return clean_space(match.group(group))
    except IndexError:
        return ""


def _text_after(value: str, marker: str, end_marker: str = "") -> str:
    folded = value.casefold()
    pos = folded.find(marker.casefold())
    if pos < 0:
        return ""
    tail = value[pos + len(marker):]
    if end_marker:
        end = tail.casefold().find(end_marker.casefold())
        if end >= 0:
            tail = tail[:end]
    else:
        # Marker-only extraction is line-oriented. There is intentionally no hidden
        # character truncation; long descriptions remain complete on their source line.
        tail = tail.lstrip(" \t:-–—\r\n")
        tail = re.split(r"[\r\n]", tail, maxsplit=1)[0]
    return clean_space(tail)


def _meta_lookup(soup: BeautifulSoup, expression: str) -> str:
    expression = expression.strip()
    if "=" in expression:
        key, value = expression.split("=", 1)
        tag = soup.find("meta", attrs={key.strip(): value.strip()})
        if tag is None and key.strip() == "itemprop":
            tag = soup.find(attrs={"itemprop": value.strip()})
    else:
        tag = soup.find("meta", attrs={"name": expression}) or soup.find("meta", attrs={"property": expression})
    if tag:
        return clean_space(str(tag.get("content") or tag.get("datetime") or tag.get_text(" ", strip=True) or ""))
    return ""


def _json_ld_values(context: PageContext, keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    stack: list[Any] = list(context.json_ld)
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in keys and isinstance(value, (str, int, float)):
                    values.append(str(value))
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return values


def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            node = soup.select_one(selector)
        except Exception:
            continue
        if node:
            value = clean_space(node.get_text(" ", strip=True))
            if value:
                return value
    return ""


def _date_result(raw: str, method: str, confidence: str, plan: ExtractionPlan, date_format: str = "") -> ExtractionResult:
    parsed = parse_date(raw, date_format, day_first=plan.profile.day_first_dates)
    return ExtractionResult(
        value=parsed.value,
        method=method,
        confidence=confidence if parsed.value else "none",
        raw_value=raw,
        precision=parsed.precision,
    )


def _auto_post_number(context: PageContext) -> ExtractionResult:
    try:
        path = urlsplit(context.url).path
    except Exception:
        path = context.url
    content_path = context.plan.profile.content_path
    pos = path.find(content_path)
    tail = path[pos + len(content_path):] if pos >= 0 else path
    numbers = re.findall(r"(?<!\d)(\d{1,20})(?!\d)", tail)
    if not numbers:
        return ExtractionResult(method="auto:url_numeric", confidence="none")
    value = numbers[-1]
    return ExtractionResult(value, "auto:url_numeric", "medium", raw_value=value)


def _auto_title(context: PageContext) -> ExtractionResult:
    soup = context.soup
    for expression in ("property=og:title", "name=twitter:title"):
        value = _meta_lookup(soup, expression)
        if value:
            return ExtractionResult(value, f"auto:meta:{expression}", "high", raw_value=value)
    value = _first_text(soup, ["h1", ".title", "#title", "h2.title", "h2"])
    if value:
        return ExtractionResult(value, "auto:visible_title", "medium", raw_value=value)
    if soup.title:
        value = clean_space(soup.title.get_text(" ", strip=True))
        if value:
            return ExtractionResult(value, "auto:html_title", "medium", raw_value=value)
    return ExtractionResult(method="auto:title", confidence="none")


def _auto_description(context: PageContext) -> ExtractionResult:
    soup = context.soup
    for expression in ("name=description", "property=og:description", "name=twitter:description"):
        value = _meta_lookup(soup, expression)
        if value:
            return ExtractionResult(value, f"auto:meta:{expression}", "high", raw_value=value)
    value = _first_text(soup, [".description", "#description", ".summary", ".excerpt", ".media-description"])
    if value:
        return ExtractionResult(value, "auto:visible_description", "medium", raw_value=value)
    return ExtractionResult(method="auto:description", confidence="none")


def _auto_category(context: PageContext) -> ExtractionResult:
    soup = context.soup
    value = _meta_lookup(soup, "property=article:section")
    if value:
        return ExtractionResult(value, "auto:meta:article:section", "high", raw_value=value)
    node = soup.find(attrs={"rel": lambda value: value and "category" in str(value).casefold()})
    if node:
        value = clean_space(node.get_text(" ", strip=True))
        if value:
            return ExtractionResult(value, "auto:rel_category", "medium", raw_value=value)
    value = _first_text(soup, [".category", "#category", ".categories", ".section", ".breadcrumb a:last-child"])
    if value:
        return ExtractionResult(value, "auto:visible_category", "medium", raw_value=value)
    return ExtractionResult(method="auto:category", confidence="none")


def _auto_date(context: PageContext) -> ExtractionResult:
    for candidate in _json_ld_values(context, ("datePublished", "dateCreated", "uploadDate")):
        result = _date_result(candidate, "auto:json_ld", "high", context.plan)
        if result.value:
            return result
    for expression in (
        "property=article:published_time", "name=pubdate", "name=publishdate",
        "name=date", "itemprop=datePublished",
    ):
        value = _meta_lookup(context.soup, expression)
        result = _date_result(value, f"auto:meta:{expression}", "high", context.plan)
        if result.value:
            return result
    for node in context.soup.find_all("time"):
        value = str(node.get("datetime") or node.get_text(" ", strip=True) or "")
        result = _date_result(value, "auto:time", "high", context.plan)
        if result.value:
            return result
    for selector in (".date", "#date", ".posted", ".post-date", ".published", "[class*='date']", "[id*='date']"):
        try:
            nodes = context.soup.select(selector)
        except Exception:
            continue
        for node in nodes[:10]:
            value = clean_space(node.get_text(" ", strip=True))
            result = _date_result(value, f"auto:visible:{selector}", "medium", context.plan)
            if result.value:
                return result
    return ExtractionResult(method="auto:date", confidence="none")


def extract_one(field_name: str, compiled: CompiledRule, context: PageContext) -> ExtractionResult:
    rule = compiled.rule
    try:
        if rule.method == "auto":
            if field_name == "post_number":
                return _auto_post_number(context)
            if field_name == "posted_date":
                return _auto_date(context)
            if field_name == "title":
                return _auto_title(context)
            if field_name == "category":
                return _auto_category(context)
            if field_name == "description":
                return _auto_description(context)

        if rule.method == "css_selector":
            node = context.soup.select_one(rule.pattern)
            if not node:
                return ExtractionResult(method="css_selector", confidence="none")
            raw = str(node.get(rule.attribute) or "") if rule.attribute else node.get_text(" ", strip=True)
            raw = clean_space(raw)
            if field_name == "posted_date":
                return _date_result(raw, "css_selector", "high", context.plan, rule.date_format)
            return ExtractionResult(raw, "css_selector", "high" if raw else "none", raw_value=raw)

        if rule.method == "meta":
            raw = _meta_lookup(context.soup, rule.pattern)
            if field_name == "posted_date":
                return _date_result(raw, "meta", "high", context.plan, rule.date_format)
            return ExtractionResult(raw, "meta", "high" if raw else "none", raw_value=raw)

        source = context.url if rule.method == "url_regex" else _source_value(rule, context)
        if rule.method in {"regex", "url_regex"}:
            assert compiled.regex is not None
            raw = _regex_extract(source, compiled.regex, rule.group)
        elif rule.method in {"text_after", "text_between"}:
            raw = _text_after(source, rule.pattern, rule.end_marker)
        else:
            raw = ""
        if field_name == "posted_date":
            return _date_result(raw, rule.method, "high", context.plan, rule.date_format)
        return ExtractionResult(raw, rule.method, "high" if raw else "none", raw_value=raw)
    except Exception as exc:
        return ExtractionResult(method=rule.method, confidence="none", error=str(exc))


def extract_fields(
    html: str | bytes,
    url: str,
    profile: SiteProfile | None = None,
    *,
    plan: ExtractionPlan | None = None,
) -> dict[str, ExtractionResult]:
    if plan is None:
        if profile is None:
            raise ValueError("profile or compiled extraction plan is required")
        plan = compile_extraction_plan(profile)
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    context = PageContext(html=html, url=url, plan=plan)
    results: dict[str, ExtractionResult] = {}
    for field_name in FIELD_NAMES:
        results[field_name] = extract_one(field_name, plan.rules[field_name], context)
    return results
