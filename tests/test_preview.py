from archive_builder.builder.preview import parse_preview_url


def test_parse_wayback_preview_url():
    original, timestamp = parse_preview_url("https://web.archive.org/web/20090102030405id_/http://nothingtoxic.com/media/123")
    assert timestamp == "20090102030405"
    assert original == "http://nothingtoxic.com/media/123"
