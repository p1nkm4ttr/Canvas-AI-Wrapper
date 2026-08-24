"""Syllabus resolution and grade-weight recovery.

The syllabus is the highest-information document in any course, and at this
institution the real one is usually an uploaded PDF, not the Canvas field.
Resolution order (build brief): the syllabus_body field, then a
syllabus-looking file found via the files listing or the module walk,
extracted with the step-3 pipeline.

Weight parsing is deliberately conservative: a confidently wrong grade
breakdown is worse than none, so parsed percentages are returned only when
they sum to roughly 100.
"""

import re
from typing import Any

from .client import fetch_all_paginated_results, make_canvas_request
from .files import get_file_text_cached
from .text import strip_html_tags

# Filenames that look like a syllabus / course outline.
_SYLLABUS_NAME = re.compile(r"syllabus|course\s*outline|course\s*info", re.IGNORECASE)


def _is_err(x: Any) -> bool:
    return isinstance(x, dict) and "error" in x


async def _find_syllabus_file(course_id: str | int) -> tuple[int, str] | None:
    """Locate a syllabus-looking file: (file_id, display_name) or None.

    Tries the files listing first (with Canvas's own search), then falls back
    to walking module File items — the listing is instructor-restricted in
    some courses while item-level access still works (measured live).
    """
    files = await fetch_all_paginated_results(
        f"/courses/{course_id}/files",
        {"search_term": "syllabus", "per_page": 100},
    )
    if isinstance(files, list) and files:
        best = min(files, key=lambda f: len(f.get("display_name") or ""))
        if best.get("id"):
            return best["id"], best.get("display_name") or "syllabus"

    modules = await fetch_all_paginated_results(
        f"/courses/{course_id}/modules", {"include[]": "items", "per_page": 100}
    )
    if isinstance(modules, list):
        candidates = [
            (item["content_id"], item.get("title") or "")
            for m in modules
            for item in (m.get("items") or [])
            if item.get("type") == "File"
            and item.get("content_id")
            and _SYLLABUS_NAME.search(item.get("title") or "")
        ]
        if candidates:
            return candidates[0]
    return None


async def resolve_syllabus(
    course_id: str | int, skip_field: bool = False
) -> dict[str, Any]:
    """The course syllabus text, from the first source that works.

    Returns {source: "field"|"file"|"none", text, url, name?, note?}.
    Text is plain; the raw HTML of the field source is under "html".

    skip_field jumps straight to the file search — used by weight recovery
    when the field exists but carries no breakdown (measured live: a course
    whose field is an intro blurb while the real syllabus is a PDF).
    """
    if not skip_field:
        detail = await make_canvas_request(
            "get", f"/courses/{course_id}", params={"include[]": "syllabus_body"}
        )
        if _is_err(detail):
            return {"source": "none", "text": "", "url": "", "note": detail["error"]}

        body = (detail.get("syllabus_body") or "").strip()
        if body:
            return {
                "source": "field",
                "text": strip_html_tags(body),
                "html": body,
                "url": f"/courses/{course_id}/assignments/syllabus",
            }

    found = await _find_syllabus_file(course_id)
    if found is not None:
        file_id, name = found
        extracted = await get_file_text_cached(file_id, int(course_id))
        if not _is_err(extracted) and extracted["status"] == "ok" and extracted["text"]:
            return {
                "source": "file",
                "text": extracted["text"],
                "name": extracted["name"],
                "url": f"/courses/{course_id}/files/{file_id}",
            }
        note = (
            extracted.get("note") or extracted.get("error", "")
            if isinstance(extracted, dict) else ""
        )
        return {
            "source": "none",
            "text": "",
            "url": f"/courses/{course_id}/files/{file_id}",
            "note": f"Found a syllabus-looking file ('{name}') but could not extract it. {note}".strip(),
        }

    return {
        "source": "none",
        "text": "",
        "url": "",
        "note": (
            "No syllabus in the Canvas field and no syllabus-looking file in "
            "this course. At Habib, syllabi are often hosted on Simple Syllabus "
            "(https://habib.simplesyllabus.com — behind university SSO, not "
            "readable by this tool; check the course's Simple Syllabus link in "
            "Canvas's course navigation). A syllabus read there can be saved "
            "as a PDF into the course space for extraction."
        ),
    }


# ---------------------------------------------------------------- weights ---

# A component line ends with its single NN% (or NN.N%) figure — prose that
# merely mentions a percentage mid-sentence does not (measured live: "papers
# will be weighted at 15%. A second final..." sat right next to the real
# "(a) Papers: 35%" lines and inflated the total past any confidence gate).
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_END_ANCHORED = re.compile(r"\d{1,3}(?:\.\d+)?\s*%[\s.)\]]*$")
# Lines that are grade-scale rows ("A 90-100%", "B+: 80% - 85%"), not weights.
_GRADE_SCALE = re.compile(
    r"(^\s*[A-F][+\-]?\s*[:=]?\s*\d)|(\d\s*%?\s*[-–—]\s*\d{1,3}\s*%)", re.IGNORECASE
)
# Prose has verbs; component labels are noun phrases.
_PROSE_WORDS = re.compile(
    r"\b(will|is|are|be|was|were|worth|weighted|required|held|than|must|risk)\b",
    re.IGNORECASE,
)
# "(a) Papers" / "1. Papers" / "b) Papers" list markers.
_LIST_MARKER = re.compile(r"^\(?[a-zA-Z0-9]{1,2}[).]\s+")
_LABEL_JUNK = re.compile(r"^[\s\d.:•*\-–—()\[\]|]+|[\s.:•*\-–—()\[\]|]+$")

MAX_LINE_LEN = 80
MIN_COMPONENTS = 2
MAX_COMPONENTS = 12
TOTAL_RANGE = (95.0, 105.0)


def parse_weights(text: str) -> list[dict[str, Any]]:
    """Parse an assessment breakdown out of syllabus text, conservatively.

    A component is a short line that ENDS with its one percentage and whose
    label reads as a noun phrase; grade-scale rows and prose sentences are
    excluded. Returns [] unless components sum to roughly 100 — prefer
    returning nothing to guessing.
    """
    components: list[dict[str, Any]] = []
    seen_labels: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) > MAX_LINE_LEN:
            continue
        if _GRADE_SCALE.search(line):
            continue
        if not _END_ANCHORED.search(line):
            continue
        percents = _PERCENT.findall(line)
        if len(percents) != 1:
            continue
        value = float(percents[0])
        if not (0 < value <= 70):
            continue  # a single component above 70% is almost surely a scale row
        label = _PERCENT.sub("", line)
        label = _LIST_MARKER.sub("", label.strip())
        label = _LABEL_JUNK.sub("", label).strip()
        if not (2 <= len(label) <= 60):
            continue
        if _PROSE_WORDS.search(label):
            continue
        key = label.lower()
        if key in seen_labels:
            continue
        seen_labels.add(key)
        components.append({"name": label, "percent": value})

    if not (MIN_COMPONENTS <= len(components) <= MAX_COMPONENTS):
        return []
    total = sum(c["percent"] for c in components)
    if not (TOTAL_RANGE[0] <= total <= TOTAL_RANGE[1]):
        return []
    return components
