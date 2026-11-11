from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import ProjectConfig, save_project_config
from .constants import CONFIG_NAME, DATABASE_NAME
from .database.connection import open_database


PROJECT_DIRS = ("captures", "exports")


@dataclass(frozen=True, slots=True)
class ProjectScaffold:
    root: Path
    config_path: Path
    database_path: Path
    captures_dir: Path
    exports_dir: Path


def safe_project_folder_name(value: str) -> str:
    """Return a cross-platform-safe project folder name."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", (value or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        raise ValueError("enter a project name")
    if name.casefold() in {
        "con", "prn", "aux", "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }:
        name += " Project"
    return name


def project_root(parent: Path, project_name: str) -> Path:
    return Path(parent).expanduser().resolve() / safe_project_folder_name(project_name)


def create_project_scaffold(config: ProjectConfig, *, allow_existing_empty: bool = True) -> ProjectScaffold:
    """Create every persistent file/directory Archive Builder needs for a new project.

    The config file and SQLite database are created immediately so an Open Project action
    can use the folder before any indexing or downloads have started.
    """
    cfg = config.normalized()
    root = cfg.output_dir
    config_path = root / CONFIG_NAME
    database_path = root / DATABASE_NAME

    if config_path.exists() or database_path.exists():
        raise FileExistsError(f"An Archive Builder project already exists in {root}")

    if root.exists():
        has_contents = any(root.iterdir())
        if has_contents:
            raise FileExistsError(f"Project folder is not empty: {root}")
        if not allow_existing_empty:
            raise FileExistsError(f"Project folder already exists: {root}")

    root.mkdir(parents=True, exist_ok=True)
    captures_dir = root / "captures"
    exports_dir = root / "exports"
    captures_dir.mkdir(exist_ok=True)
    exports_dir.mkdir(exist_ok=True)

    save_project_config(cfg)
    database: sqlite3.Connection | None = None
    try:
        database = open_database(root)
    finally:
        if database is not None:
            database.close()

    return ProjectScaffold(
        root=root,
        config_path=config_path,
        database_path=database_path,
        captures_dir=captures_dir,
        exports_dir=exports_dir,
    )
