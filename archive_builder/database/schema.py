from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 3

# Retained as the schema-1 bootstrap used by migration tests and very old projects.
BASE_SCHEMA_SQL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_info(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_url TEXT NOT NULL UNIQUE,
    first_capture_timestamp TEXT,
    representative_timestamp TEXT,
    representative_mimetype TEXT,
    representative_statuscode TEXT,
    download_state TEXT NOT NULL DEFAULT 'pending',
    extraction_state TEXT NOT NULL DEFAULT 'pending',
    extraction_fingerprint TEXT,
    local_path TEXT,
    http_status INTEGER,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pages_first_capture_idx ON pages(first_capture_timestamp,id);
CREATE INDEX IF NOT EXISTS pages_download_idx ON pages(download_state,representative_timestamp,id);
CREATE INDEX IF NOT EXISTS pages_extract_idx ON pages(extraction_state,download_state,id);

CREATE TABLE IF NOT EXISTS extracted_fields(
    page_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    value TEXT,
    method TEXT,
    confidence TEXT,
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(page_id,field_name),
    FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS extracted_fields_name_value_idx ON extracted_fields(field_name,value);

CREATE TABLE IF NOT EXISTS index_state(
    phase TEXT NOT NULL,
    year INTEGER NOT NULL,
    query_signature TEXT NOT NULL,
    resume_key TEXT,
    complete INTEGER NOT NULL DEFAULT 0,
    seen INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(phase,year,query_signature)
);

CREATE TABLE IF NOT EXISTS operation_runs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS operation_runs_status_idx ON operation_runs(status,id);

CREATE TABLE IF NOT EXISTS errors(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER,
    operation TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    retryable INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS errors_operation_idx ON errors(operation,category,id);
"""

DEDUP_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS duplicate_groups(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'review',
    reviewed INTEGER NOT NULL DEFAULT 0,
    canonical_page_id INTEGER,
    first_capture_timestamp TEXT,
    member_count INTEGER NOT NULL DEFAULT 0,
    post_number TEXT,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(canonical_page_id) REFERENCES pages(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS duplicate_groups_status_idx ON duplicate_groups(status,decision,id);
CREATE INDEX IF NOT EXISTS duplicate_groups_canonical_idx ON duplicate_groups(canonical_page_id,id);

CREATE TABLE IF NOT EXISTS duplicate_group_members(
    group_id INTEGER NOT NULL,
    page_id INTEGER NOT NULL UNIQUE,
    is_canonical INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY(group_id,page_id),
    FOREIGN KEY(group_id) REFERENCES duplicate_groups(id) ON DELETE CASCADE,
    FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS duplicate_group_members_group_idx ON duplicate_group_members(group_id,is_canonical,page_id);
"""

V3_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS page_aliases(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    original_url TEXT NOT NULL UNIQUE,
    urlkey TEXT,
    first_capture_timestamp TEXT,
    representative_timestamp TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS page_aliases_page_idx ON page_aliases(page_id,id);
CREATE INDEX IF NOT EXISTS page_aliases_urlkey_idx ON page_aliases(urlkey,page_id);

CREATE TABLE IF NOT EXISTS index_coverage(
    phase TEXT NOT NULL,
    range_start TEXT NOT NULL,
    range_end TEXT NOT NULL,
    strategy TEXT NOT NULL,
    unit_key TEXT NOT NULL,
    query_signature TEXT NOT NULL,
    resume_key TEXT,
    complete INTEGER NOT NULL DEFAULT 0,
    seen INTEGER NOT NULL DEFAULT 0,
    response_fingerprint TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(phase,range_start,range_end,strategy,unit_key,query_signature)
);
CREATE INDEX IF NOT EXISTS index_coverage_complete_idx ON index_coverage(phase,complete,updated_at);

CREATE TABLE IF NOT EXISTS project_state(
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS duplicate_review_history(
    identity_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewed INTEGER NOT NULL,
    canonical_page_id INTEGER,
    member_signature TEXT,
    updated_at TEXT NOT NULL
);
"""


def _column_names(database: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in database.execute(f"PRAGMA table_info({table})")}


def _add_column(database: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _column_names(database, table):
        database.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _migrate_to_v2(database: sqlite3.Connection) -> None:
    database.executescript(DEDUP_SCHEMA_SQL)


def _migrate_to_v3(database: sqlite3.Connection) -> None:
    # Capture/server identity and replay provenance. Unknown legacy values remain NULL.
    for definition in (
        "urlkey TEXT",
        "selected_capture_timestamp TEXT",
        "selected_capture_digest TEXT",
        "selected_capture_length TEXT",
        "intended_local_path TEXT",
        "actual_replay_timestamp TEXT",
        "replay_final_url TEXT",
        "response_mimetype TEXT",
        "response_encoding TEXT",
        "content_classification TEXT",
        "classification_reason TEXT",
        "source_size INTEGER",
        "source_sha256 TEXT",
        "source_identity TEXT",
        "extraction_source_identity TEXT",
        "extraction_engine_version TEXT",
        "download_started_at TEXT",
    ):
        _add_column(database, "pages", definition)

    for definition in (
        "raw_value TEXT",
        "precision TEXT",
    ):
        _add_column(database, "extracted_fields", definition)

    for definition in (
        "identity_key TEXT",
        "member_signature TEXT",
    ):
        _add_column(database, "duplicate_groups", definition)

    database.executescript(V3_SCHEMA_SQL)

    # v2 constrained a page to only one duplicate group. v3 allows an exact subset
    # and a separate conflicting-anchor review group to coexist.
    sql = database.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='duplicate_group_members'"
    ).fetchone()
    create_sql = str(sql[0] or "") if sql else ""
    if "page_id INTEGER NOT NULL UNIQUE" in create_sql:
        database.executescript(
            """
            ALTER TABLE duplicate_group_members RENAME TO duplicate_group_members_v2;
            CREATE TABLE duplicate_group_members(
                group_id INTEGER NOT NULL,
                page_id INTEGER NOT NULL,
                is_canonical INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY(group_id,page_id),
                FOREIGN KEY(group_id) REFERENCES duplicate_groups(id) ON DELETE CASCADE,
                FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
            );
            INSERT OR IGNORE INTO duplicate_group_members(group_id,page_id,is_canonical,created_at)
                SELECT group_id,page_id,is_canonical,created_at FROM duplicate_group_members_v2;
            DROP TABLE duplicate_group_members_v2;
            CREATE INDEX IF NOT EXISTS duplicate_group_members_group_idx
                ON duplicate_group_members(group_id,is_canonical,page_id);
            CREATE INDEX IF NOT EXISTS duplicate_group_members_page_idx
                ON duplicate_group_members(page_id,group_id);
            """
        )

    database.execute("CREATE INDEX IF NOT EXISTS pages_urlkey_idx ON pages(urlkey,id)")
    database.execute("CREATE INDEX IF NOT EXISTS pages_source_identity_idx ON pages(source_identity,id)")
    database.execute("CREATE INDEX IF NOT EXISTS pages_selected_capture_idx ON pages(selected_capture_timestamp,id)")
    database.execute("CREATE INDEX IF NOT EXISTS duplicate_groups_identity_idx ON duplicate_groups(identity_key,id)")

    # Every legacy primary URL is also an alias. This preserves all old records without
    # guessing a server urlkey that was never stored.
    database.execute(
        """
        INSERT OR IGNORE INTO page_aliases(page_id,original_url,urlkey,first_capture_timestamp,representative_timestamp,created_at,updated_at)
        SELECT id,original_url,urlkey,first_capture_timestamp,representative_timestamp,created_at,updated_at
        FROM pages
        """
    )
    database.execute(
        "UPDATE pages SET selected_capture_timestamp=representative_timestamp "
        "WHERE selected_capture_timestamp IS NULL AND representative_timestamp IS NOT NULL"
    )


def initialize_schema(database: sqlite3.Connection) -> None:
    database.executescript(BASE_SCHEMA_SQL)
    row = database.execute("SELECT version FROM schema_info LIMIT 1").fetchone()
    if row is None:
        _migrate_to_v2(database)
        _migrate_to_v3(database)
        database.execute("INSERT INTO schema_info(version) VALUES(?)", (SCHEMA_VERSION,))
        database.commit()
        return

    version = int(row[0])
    if version < 1 or version > SCHEMA_VERSION:
        raise RuntimeError(f"unsupported Archive Builder database schema: {version}")
    if version == 1:
        _migrate_to_v2(database)
        version = 2
    if version == 2:
        _migrate_to_v3(database)
        version = 3
    database.execute("UPDATE schema_info SET version=?", (version,))
    database.commit()
