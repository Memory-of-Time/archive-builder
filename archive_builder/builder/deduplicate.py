from __future__ import annotations

import html as html_lib
import re
import sqlite3
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from urllib.parse import urlsplit

from ..config import ProjectConfig
from ..database.repositories import mark_dedupe_current
from ..events import ProgressEvent
from ..utils import clean_space, stable_json_hash, utc_now


def normalize_duplicate_value(field_name: str, value: str | None) -> str:
    text = html_lib.unescape(str(value or ""))
    text = unicodedata.normalize("NFKC", text)
    text = clean_space(text).casefold()
    if not text:
        return ""
    if field_name == "post_number":
        compact = re.sub(r"\s+", "", text)
        if compact.isdigit():
            # Duplicate matching may ignore superficial zero padding. The exported raw
            # Post Number remains untouched in extracted_fields.
            return str(int(compact))
        return compact
    if field_name == "title":
        text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
        return clean_space(text)
    return text


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").casefold()
    except Exception:
        return ""


def _page_rows(database: sqlite3.Connection) -> list[dict[str, object]]:
    rows = database.execute(
        """
        SELECT p.id,p.original_url,p.urlkey,p.first_capture_timestamp,p.extraction_state,
               MAX(CASE WHEN e.field_name='post_number' THEN e.value END) AS post_number,
               MAX(CASE WHEN e.field_name='posted_date' THEN e.value END) AS posted_date,
               MAX(CASE WHEN e.field_name='title' THEN e.value END) AS title,
               MAX(CASE WHEN e.field_name='category' THEN e.value END) AS category,
               MAX(CASE WHEN e.field_name='description' THEN e.value END) AS description
        FROM pages p
        LEFT JOIN extracted_fields e ON e.page_id=p.id
        WHERE p.extraction_state='complete'
        GROUP BY p.id
        ORDER BY p.id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _canonical_page(rows: list[dict[str, object]], preferred_domain: str) -> dict[str, object]:
    def key(row: dict[str, object]):
        completeness = sum(
            1 for name in ("post_number", "posted_date", "title", "category", "description")
            if str(row.get(name) or "").strip()
        )
        preferred = 1 if _host(str(row.get("original_url") or "")) == preferred_domain else 0
        timestamp = str(row.get("first_capture_timestamp") or "99999999999999")
        return (-completeness, -preferred, timestamp, str(row.get("original_url") or ""))

    return min(rows, key=key)


def _cross_host_ok(config: ProjectConfig, members: list[dict[str, object]]) -> bool:
    if not config.dedup.cross_host_only:
        return True
    hosts = {_host(str(row.get("original_url") or "")) for row in members}
    hosts.discard("")
    return len(hosts) >= 2


def _make_group(
    cfg: ProjectConfig,
    status: str,
    identity: dict[str, str],
    members: list[dict[str, object]],
) -> dict[str, object]:
    canonical = _canonical_page(members, cfg.dedup.preferred_domain or cfg.site.domain)
    first_capture = min(
        (str(row.get("first_capture_timestamp") or "") for row in members if str(row.get("first_capture_timestamp") or "")),
        default="",
    )
    identity_key = f"{status}:" + stable_json_hash(
        {"fields": tuple(cfg.dedup.match_fields), "identity": identity}, length=32
    )
    member_ids = sorted(int(row["id"]) for row in members)
    member_signature = stable_json_hash(member_ids, length=32)
    return {
        "match_key": identity_key,
        "identity_key": identity_key,
        "member_signature": member_signature,
        "status": status,
        "confidence": "high" if status == "exact" else "medium",
        "decision": "merge" if status == "exact" else "review",
        "canonical_page_id": int(canonical["id"]),
        "first_capture_timestamp": first_capture,
        "member_count": len(members),
        "post_number": str(canonical.get("post_number") or ""),
        "title": str(canonical.get("title") or ""),
        "members": members,
    }


def _candidate_groups(config: ProjectConfig, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Build exact full-identity subsets plus conservative same-anchor review groups.

    A conflicting same-anchor record no longer destroys an otherwise exact subset. For
    example A/title-X on two hosts remains an exact merge candidate even if a third host
    contains A/title-Y; the wider anchor conflict is a separate probable review group.
    """
    cfg = config.normalized()
    fields = tuple(cfg.dedup.match_fields)
    anchor_field = fields[0]
    buckets: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        anchor = normalize_duplicate_value(anchor_field, str(row.get(anchor_field) or ""))
        if anchor:
            buckets[anchor].append(row)

    groups: list[dict[str, object]] = []
    for anchor, members in buckets.items():
        if len(members) < 2 or not _cross_host_ok(cfg, members):
            continue

        exact_buckets: dict[tuple[str, ...], list[dict[str, object]]] = defaultdict(list)
        incomplete_or_conflicting = False
        for row in members:
            values = tuple(normalize_duplicate_value(name, str(row.get(name) or "")) for name in fields)
            if all(values):
                exact_buckets[values].append(row)
            else:
                incomplete_or_conflicting = True

        exact_member_sets: list[set[int]] = []
        for values, subset in exact_buckets.items():
            if len(subset) < 2 or not _cross_host_ok(cfg, subset):
                continue
            identity = {field_name: values[index] for index, field_name in enumerate(fields)}
            groups.append(_make_group(cfg, "exact", identity, subset))
            exact_member_sets.append({int(row["id"]) for row in subset})

        # A probable group exists when the anchor points to more than one distinct full
        # identity, some values are incomplete, or not every member belongs to the same
        # exact subset. It may overlap exact groups intentionally for review visibility.
        normalized_full = {
            tuple(normalize_duplicate_value(name, str(row.get(name) or "")) for name in fields)
            for row in members
        }
        one_exact_covers_all = any(ids == {int(row["id"]) for row in members} for ids in exact_member_sets)
        conflict = incomplete_or_conflicting or len(normalized_full) > 1 or not one_exact_covers_all
        if conflict:
            groups.append(_make_group(cfg, "probable", {anchor_field: anchor}, members))

    groups.sort(key=lambda item: (0 if item["status"] == "probable" else 1, str(item["identity_key"])))
    return groups


def _snapshot_review_history(database: sqlite3.Connection) -> None:
    now = utc_now()
    rows = database.execute(
        """
        SELECT COALESCE(identity_key,match_key) AS identity_key,id,status,decision,reviewed,
               canonical_page_id,member_signature
        FROM duplicate_groups
        """
    ).fetchall()
    for row in rows:
        identity_key = str(row["identity_key"] or "")
        if not identity_key:
            continue
        member_signature = str(row["member_signature"] or "")
        if not member_signature:
            member_ids = [
                int(item["page_id"])
                for item in database.execute(
                    "SELECT page_id FROM duplicate_group_members WHERE group_id=? ORDER BY page_id",
                    (int(row["id"]),),
                ).fetchall()
            ]
            member_signature = stable_json_hash(member_ids, length=32)
        database.execute(
            """
            INSERT INTO duplicate_review_history(
                identity_key,status,decision,reviewed,canonical_page_id,member_signature,updated_at
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(identity_key) DO UPDATE SET
                status=excluded.status,decision=excluded.decision,reviewed=excluded.reviewed,
                canonical_page_id=excluded.canonical_page_id,member_signature=excluded.member_signature,
                updated_at=excluded.updated_at
            """,
            (
                identity_key, row["status"], row["decision"], int(row["reviewed"] or 0),
                row["canonical_page_id"], member_signature, now,
            ),
        )


def detect_duplicates(
    config: ProjectConfig,
    database: sqlite3.Connection,
    callback: Callable[[ProgressEvent], None] | None = None,
) -> dict[str, int]:
    cfg = config.normalized()
    _snapshot_review_history(database)
    database.execute("DELETE FROM duplicate_group_members")
    database.execute("DELETE FROM duplicate_groups")

    if not cfg.dedup.cross_domain_enabled:
        mark_dedupe_current(database)
        database.commit()
        counts = {"groups": 0, "exact": 0, "probable": 0, "merged": 0, "review": 0}
        if callback:
            callback(ProgressEvent("dedupe", "Cross-domain duplicate detection is disabled."))
        return counts

    rows = _page_rows(database)
    groups = _candidate_groups(cfg, rows)
    history = {
        str(row["identity_key"]): dict(row)
        for row in database.execute("SELECT * FROM duplicate_review_history").fetchall()
    }
    now = utc_now()
    exact_count = probable_count = merged_count = review_count = 0

    for index, group in enumerate(groups, start=1):
        prior = history.get(str(group["identity_key"]))
        decision = str(group["decision"])
        reviewed = 0
        canonical_page_id = int(group["canonical_page_id"])
        member_ids = {int(row["id"]) for row in group["members"]}

        if prior:
            old_canonical = prior.get("canonical_page_id")
            if old_canonical is not None and int(old_canonical) in member_ids:
                canonical_page_id = int(old_canonical)
            prior_reviewed = bool(int(prior.get("reviewed") or 0))
            same_members = str(prior.get("member_signature") or "") == str(group["member_signature"])
            if prior_reviewed and same_members:
                old_decision = str(prior.get("decision") or "review")
                if old_decision in {"merge", "separate"}:
                    decision = old_decision
                    reviewed = 1
            elif prior_reviewed and not same_members:
                # Preserve the canonical selection where possible, but force a new review.
                decision = "review"
                reviewed = 0
            elif not prior_reviewed and group["status"] == "exact":
                decision = "merge"

        cursor = database.execute(
            """
            INSERT INTO duplicate_groups(
                match_key,identity_key,member_signature,status,confidence,decision,reviewed,
                canonical_page_id,first_capture_timestamp,member_count,post_number,title,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                group["match_key"], group["identity_key"], group["member_signature"], group["status"],
                group["confidence"], decision, reviewed, canonical_page_id,
                group["first_capture_timestamp"], group["member_count"], group["post_number"],
                group["title"], now, now,
            ),
        )
        group_id = int(cursor.lastrowid)
        for member in group["members"]:
            page_id = int(member["id"])
            database.execute(
                "INSERT INTO duplicate_group_members(group_id,page_id,is_canonical,created_at) VALUES(?,?,?,?)",
                (group_id, page_id, int(page_id == canonical_page_id), now),
            )

        if group["status"] == "exact":
            exact_count += 1
        else:
            probable_count += 1
        if decision == "merge":
            merged_count += 1
        elif decision == "review":
            review_count += 1
        if callback:
            callback(ProgressEvent(
                "dedupe",
                f"Duplicate group {index:,}/{len(groups):,}: {group['status']} ({group['member_count']} URLs)",
                index,
                len(groups),
            ))

    mark_dedupe_current(database)
    database.commit()
    counts = {
        "groups": len(groups), "exact": exact_count, "probable": probable_count,
        "merged": merged_count, "review": review_count,
    }
    if callback:
        callback(ProgressEvent(
            "dedupe",
            f"Duplicate analysis complete: {exact_count:,} exact, {probable_count:,} probable groups.",
            len(groups), len(groups), counts,
        ))
    return counts


def set_duplicate_decision(database: sqlite3.Connection, group_id: int, decision: str) -> None:
    decision = (decision or "").strip().casefold()
    if decision not in {"merge", "separate", "review"}:
        raise ValueError("duplicate decision must be merge, separate, or review")
    now = utc_now()
    database.execute(
        "UPDATE duplicate_groups SET decision=?,reviewed=?,updated_at=? WHERE id=?",
        (decision, int(decision != "review"), now, int(group_id)),
    )
    row = database.execute("SELECT * FROM duplicate_groups WHERE id=?", (int(group_id),)).fetchone()
    if row and row["identity_key"]:
        database.execute(
            """
            INSERT INTO duplicate_review_history(identity_key,status,decision,reviewed,canonical_page_id,member_signature,updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(identity_key) DO UPDATE SET decision=excluded.decision,reviewed=excluded.reviewed,
                canonical_page_id=excluded.canonical_page_id,member_signature=excluded.member_signature,
                status=excluded.status,updated_at=excluded.updated_at
            """,
            (
                row["identity_key"], row["status"], decision, int(decision != "review"),
                row["canonical_page_id"], row["member_signature"], now,
            ),
        )
    database.commit()


def set_duplicate_canonical(database: sqlite3.Connection, group_id: int, page_id: int) -> None:
    exists = database.execute(
        "SELECT 1 FROM duplicate_group_members WHERE group_id=? AND page_id=?",
        (int(group_id), int(page_id)),
    ).fetchone()
    if not exists:
        raise ValueError("selected page is not a member of that duplicate group")
    now = utc_now()
    database.execute(
        "UPDATE duplicate_groups SET canonical_page_id=?,reviewed=1,updated_at=? WHERE id=?",
        (int(page_id), now, int(group_id)),
    )
    database.execute("UPDATE duplicate_group_members SET is_canonical=0 WHERE group_id=?", (int(group_id),))
    database.execute(
        "UPDATE duplicate_group_members SET is_canonical=1 WHERE group_id=? AND page_id=?",
        (int(group_id), int(page_id)),
    )
    row = database.execute("SELECT * FROM duplicate_groups WHERE id=?", (int(group_id),)).fetchone()
    if row and row["identity_key"]:
        database.execute(
            """
            INSERT INTO duplicate_review_history(identity_key,status,decision,reviewed,canonical_page_id,member_signature,updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(identity_key) DO UPDATE SET canonical_page_id=excluded.canonical_page_id,
                reviewed=excluded.reviewed,decision=excluded.decision,member_signature=excluded.member_signature,
                status=excluded.status,updated_at=excluded.updated_at
            """,
            (
                row["identity_key"], row["status"], row["decision"], 1, int(page_id),
                row["member_signature"], now,
            ),
        )
    database.commit()


def duplicate_counts(database: sqlite3.Connection) -> dict[str, int]:
    row = database.execute(
        """
        SELECT COUNT(*) AS groups,
               SUM(CASE WHEN status='exact' THEN 1 ELSE 0 END) AS exact,
               SUM(CASE WHEN status='probable' THEN 1 ELSE 0 END) AS probable,
               SUM(CASE WHEN decision='merge' THEN 1 ELSE 0 END) AS merged,
               SUM(CASE WHEN decision='review' THEN 1 ELSE 0 END) AS review
        FROM duplicate_groups
        """
    ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}
