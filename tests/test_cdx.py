from pathlib import Path

from archive_builder.cdx.client import parse_cdx
from archive_builder.cdx.parameters import build_params
from archive_builder.config import new_project_config


def test_parse_cdx_resume_key():
    payload = [
        ["timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        ["20090102030405", "http://nothingtoxic.com/media/123", "text/html", "200", "ABC", "12"],
        [],
        ["resume-token"],
    ]
    result = parse_cdx(payload)
    assert result.resume_key == "resume-token"
    assert result.rows[0]["original"].endswith("/media/123")


def test_nothingtoxic_cdx_is_domain_and_path_filtered(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    params = build_params(cfg, "inventory", 2009)
    assert ("matchType", "domain") in params
    assert any(k == "filter" and "/media/" in v for k, v in params)
    assert ("collapse", "urlkey") in params


def test_cdx_collapse_urlkey_can_be_disabled(tmp_path: Path):
    cfg = new_project_config(tmp_path)
    cfg.dedup.cdx_collapse_urlkey = False
    params = build_params(cfg, "inventory", 2009)
    assert ("collapse", "urlkey") not in params
