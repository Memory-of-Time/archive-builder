import json
from pathlib import Path

import pytest

from archive_builder.config import ProjectConfig, SiteProfile, new_project_config
from archive_builder.constants import CONFIG_NAME, DATABASE_NAME
from archive_builder.project import create_project_scaffold, safe_project_folder_name
from archive_builder.utils import normalized_content_path


def test_project_scaffold_creates_required_files_and_folders(tmp_path: Path):
    root = tmp_path / "NothingToxic"
    cfg = new_project_config(root)
    scaffold = create_project_scaffold(cfg)

    assert scaffold.config_path == root / CONFIG_NAME
    assert scaffold.database_path == root / DATABASE_NAME
    assert scaffold.config_path.is_file()
    assert scaffold.database_path.is_file()
    assert scaffold.captures_dir.is_dir()
    assert scaffold.exports_dir.is_dir()

    payload = json.loads(scaffold.config_path.read_text(encoding="utf-8"))
    assert payload["site"]["domain"] == "nothingtoxic.com"
    assert payload["site"]["content_path"] == "/media/"
    assert payload["dedup"]["cdx_collapse_urlkey"] is True
    assert payload["dedup"]["cross_domain_enabled"] is True
    assert payload["dedup"]["match_fields"] == ["post_number", "title"]


def test_project_scaffold_refuses_existing_project(tmp_path: Path):
    cfg = new_project_config(tmp_path / "Existing")
    create_project_scaffold(cfg)
    with pytest.raises(FileExistsError):
        create_project_scaffold(cfg)


def test_custom_project_is_not_nothingtoxic_hardcoded(tmp_path: Path):
    cfg = ProjectConfig(
        tmp_path / "Example",
        SiteProfile(name="Example", domain="example.org", content_path="/posts/", include_subdomains=False),
    )
    create_project_scaffold(cfg)
    payload = json.loads((cfg.output_dir / CONFIG_NAME).read_text(encoding="utf-8"))
    assert payload["site"]["domain"] == "example.org"
    assert payload["site"]["content_path"] == "/posts/"
    assert payload["site"]["include_subdomains"] is False


def test_content_path_accepts_optional_wildcard():
    assert normalized_content_path("/media/*") == "/media/"
    assert normalized_content_path("media/*") == "/media/"


def test_project_folder_name_is_cross_platform_safe():
    assert safe_project_folder_name('My: Archive / 2008?') == 'My- Archive - 2008-'
