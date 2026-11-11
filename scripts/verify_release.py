from __future__ import annotations

import argparse
import ast
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def assigned_value(path: Path, name: str) -> object:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"Missing {name} in {path.relative_to(ROOT)}")


def verify_release(root: Path, tag: str | None = None) -> str:
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Invalid public release version: {version!r}")
    expected = {
        "archive_builder/constants.py": "VERSION",
        "archive_builder/__init__.py": "__version__",
    }
    for filename, name in expected.items():
        actual = assigned_value(root / filename, name)
        if actual != version:
            raise ValueError(f"{filename}: {actual!r} differs from {version!r}")
    windows = (root / "packaging/windows/version_info.txt").read_text(encoding="utf-8")
    numeric = tuple(int(part) for part in version.split(".")) + (0,)
    for name in ("filevers", "prodvers"):
        match = re.search(rf"{name}\s*=\s*(\([^)]*\))", windows)
        if not match or ast.literal_eval(match[1]) != numeric:
            raise ValueError(f"Windows {name} does not match {version}")
    for name in ("FileVersion", "ProductVersion"):
        if not re.search(rf"StringStruct\('{name}', '{re.escape(version)}'\)", windows):
            raise ValueError(f"Windows {name} does not match {version}")
    for filename in ("README.md", "RELEASE_NOTES.md"):
        if not (root / filename).read_text(encoding="utf-8").startswith(f"# Archive Builder v{version}\n"):
            raise ValueError(f"{filename} does not identify v{version}")
    if tag and tag != f"v{version}":
        raise ValueError(f"Release tag {tag!r} must match source version 'v{version}'")
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Archive Builder release metadata before packaging")
    parser.add_argument("--tag", default=None)
    args = parser.parse_args()
    try:
        version = verify_release(ROOT, args.tag)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f"Release validation failed: {exc}\n")
    print(f"Archive Builder {version}: release metadata verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
