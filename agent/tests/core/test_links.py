"""Harvesting file references and outside links from Canvas rich text."""

from canvas_mcp.core.links import harvest_external_links, harvest_file_refs

HTML = """
<p>Read <a class="instructure_file_link" title="Brief.pdf"
   href="https://canvas.school.edu/courses/5351/files/889867?verifier=abc&amp;wrap=1"
   data-api-endpoint="https://canvas.school.edu/api/v1/courses/5351/files/889867">the <b>brief</b></a>
and the <a href="/courses/5351/files/889874/download?download_frd=1">rubric</a>.</p>
<img src="/courses/5351/files/900001/preview" alt="diagram">
<p>Submit on <a href="https://forms.google.com/xyz">this form</a>; slides on
<a href="https://canvas.school.edu/courses/5351/pages/week-1">Week 1</a>;
<a href="https://forms.google.com/xyz">same form again</a>.</p>
"""


def test_anchors_come_first_with_their_own_url_and_title():
    refs = harvest_file_refs(HTML)
    assert [r["fileId"] for r in refs] == [889867, 889874, 900001]
    assert refs[0]["url"] == "https://canvas.school.edu/courses/5351/files/889867?verifier=abc&wrap=1"
    assert refs[0]["title"] == "the brief"          # tags stripped, entities decoded
    assert refs[1]["url"].endswith("/download?download_frd=1")
    assert refs[2] == {"fileId": 900001, "url": "", "title": ""}   # bare <img> reference


def test_duplicates_collapse_and_empty_is_fine():
    two = "<a href='/files/1'>a</a><a href=\"/courses/2/files/1\">b</a>"
    assert len(harvest_file_refs(two)) == 1
    assert harvest_file_refs(None) == []
    assert harvest_file_refs("<p>no links</p>") == []


def test_external_links_exclude_canvas_and_dedupe():
    links = harvest_external_links(HTML, "https://canvas.school.edu")
    assert links == [{"title": "this form", "url": "https://forms.google.com/xyz"}]


def test_external_links_fall_back_to_the_url_as_title():
    assert harvest_external_links('<a href="https://x.example/a"></a>', "https://c")[0]["title"] == "https://x.example/a"
