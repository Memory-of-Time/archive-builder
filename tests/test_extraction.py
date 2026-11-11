from archive_builder.config import ExtractionRule, SiteProfile
from archive_builder.extraction import extract_fields

HTML = """
<html><head>
<meta property="og:title" content="Test Clip">
<meta name="description" content="A test description">
<meta property="article:section" content="Accidents">
</head><body><div class="posted">Posted: June 14, 2008</div></body></html>
"""


def profile(**rules):
    base = SiteProfile(domain="nothingtoxic.com", content_path="/media/")
    base.rules = {name: ExtractionRule(method="auto") for name in ("post_number","posted_date","title","category","description")}
    base.rules.update(rules)
    return base.normalized()


def test_auto_extracts_common_fields():
    results = extract_fields(HTML, "http://nothingtoxic.com/media/12345/test", profile())
    assert results["post_number"].value == "12345"
    assert results["posted_date"].value == "2008-06-14"
    assert results["title"].value == "Test Clip"
    assert results["category"].value == "Accidents"
    assert results["description"].value == "A test description"


def test_configurable_text_after_and_url_regex():
    p = profile(
        posted_date=ExtractionRule(method="text_after", source="text", pattern="Posted:", date_format="%B %d, %Y"),
        post_number=ExtractionRule(method="url_regex", source="url", pattern=r"/media/(\d+)/", group=1),
    )
    results = extract_fields(HTML, "http://nothingtoxic.com/media/54321/test", p)
    assert results["post_number"].value == "54321"
    assert results["posted_date"].value == "2008-06-14"
