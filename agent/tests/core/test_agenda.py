"""Tests for agenda status derivation (verified-fact behavior)."""

from canvas_mcp.agenda import derive_status


def _item(**sub):
    return {"submissions": sub}


def test_graded_but_not_submitted_is_reported_honestly():
    # Verified fact: items can be graded but NOT submitted.
    assert derive_status(_item(graded=True, submitted=False)) == "graded (not submitted)"


def test_submitted():
    assert derive_status(_item(submitted=True)) == "submitted"


def test_late_submission():
    assert derive_status(_item(submitted=True, late=True)) == "late"


def test_missing():
    assert derive_status(_item(missing=True)) == "missing"


def test_excused_wins():
    assert derive_status(_item(excused=True, missing=True)) == "excused"


def test_no_submission_block_degrades_gracefully():
    # Every Canvas field is effectively optional.
    assert derive_status({"submissions": None}) == "-"
    assert derive_status({}) == "-"


def test_untouched_item_is_todo():
    assert derive_status(_item()) == "todo"
