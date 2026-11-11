from __future__ import annotations

APP_NAME = "Archive Builder"
VERSION = "1.0.0"
DATABASE_NAME = "archive_builder.sqlite3"
CONFIG_NAME = "archive_builder.json"
CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_REPLAY_PREFIX = "https://web.archive.org/web"
DEFAULT_USER_AGENT = f"ArchiveBuilder/{VERSION} public web archive research client"
EXTRACTOR_VERSION = "2"
FIELD_NAMES = ("post_number", "posted_date", "title", "category", "description")
FIELD_LABELS = {
    "post_number": "Post Number",
    "posted_date": "Posted Date",
    "title": "Title",
    "category": "Category",
    "description": "Description",
}
CSV_COLUMNS = (
    "Post Number",
    "Posted Date",
    "Title",
    "Category",
    "Description",
    "URL",
    "First Capture Date",
)
