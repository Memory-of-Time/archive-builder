from pathlib import Path

from archive_builder.config import new_project_config


def test_nothingtoxic_starter_profile(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    assert cfg.site.domain == "nothingtoxic.com"
    assert cfg.site.include_subdomains is True
    assert cfg.site.content_path == "/media/"
    assert list(cfg.site.rules) == ["post_number", "posted_date", "title", "category", "description"]
    assert cfg.dedup.cdx_collapse_urlkey is True
    assert cfg.dedup.cross_domain_enabled is True
    assert tuple(cfg.dedup.match_fields) == ("post_number", "title")
    assert cfg.dedup.preferred_domain == "nothingtoxic.com"
