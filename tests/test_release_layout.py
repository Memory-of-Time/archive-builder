from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_release_files_present():
    expected = [
        ".github/workflows/tests.yml",
        ".github/workflows/build-and-release.yml",
        "scripts/build_windows.ps1",
        "scripts/build_linux.sh",
        "scripts/build_macos.sh",
        "packaging/windows/install.ps1",
        "packaging/linux/install.sh",
        "requirements-build.txt",
    ]
    for rel in expected:
        assert (ROOT / rel).is_file(), rel


def test_readme_has_three_stable_download_links():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    links = [
        "https://github.com/Memory-of-Time/archive-builder/releases/latest/download/ArchiveBuilder-Windows-x64.zip",
        "https://github.com/Memory-of-Time/archive-builder/releases/latest/download/ArchiveBuilder-Linux-x64.tar.gz",
        "https://github.com/Memory-of-Time/archive-builder/releases/latest/download/ArchiveBuilder-macOS-Universal.zip",
    ]
    for link in links:
        assert link in text
    assert max(text.index(link) for link in links) < 1600


def test_public_version_is_consistent():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    constants = (ROOT / "archive_builder/constants.py").read_text(encoding="utf-8")
    init = (ROOT / "archive_builder/__init__.py").read_text(encoding="utf-8")
    assert 'version = "1.0.0"' in pyproject
    assert 'VERSION = "1.0.0"' in constants
    assert '__version__ = "1.0.0"' in init


def test_release_workflow_names_all_platform_assets():
    text = (ROOT / ".github/workflows/build-and-release.yml").read_text(encoding="utf-8")
    for asset in (
        "ArchiveBuilder-Windows-x64.zip",
        "ArchiveBuilder-Linux-x64.tar.gz",
        "ArchiveBuilder-macOS-Universal.zip",
    ):
        assert asset in text
    assert "push:" in text and 'tags:' in text


def test_license_names_archive_builder():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Archive Builder contributors" in text
    assert "Archive Scout contributors" not in text


def test_cli_version_works_without_a_project():
    result = subprocess.run([sys.executable, "-m", "archive_builder", "--version"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Archive Builder 1.0.0"


def test_release_verifier_accepts_matching_tag_and_rejects_a_wrong_tag():
    command = [sys.executable, str(ROOT / "scripts/verify_release.py"), "--tag"]
    accepted = subprocess.run([*command, "v1.0.0"], cwd=ROOT, capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stderr
    rejected = subprocess.run([*command, "v0.1.3"], cwd=ROOT, capture_output=True, text=True)
    assert rejected.returncode == 1
    assert "must match source version" in rejected.stderr
