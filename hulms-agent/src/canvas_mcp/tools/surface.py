"""The HULMS tool surface: eleven tools, exactly as the build brief specifies.

Design rules (from CLAUDE.md):
- Every `course` parameter accepts a name, code, or id, resolved internally.
- Date arithmetic happens here (daysUntil), never in the model.
- Every item carries an absolute html_url so claims are traceable.
- Canvas-authored free text is fenced as untrusted before it reaches the model.
- Prefer returning nothing (with an honest note) to returning a guess.
- Past ~30 items, summarise rather than dump.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..agenda import collect_agenda, derive_status, local_tz
from ..core.cache import resolve_course
from ..core.client import absolute_url, fetch_all_paginated_results, make_canvas_request
from ..core.syllabus import parse_weights, resolve_syllabus
from ..core.text import strip_html_tags
from ..core.untrusted_content import fence_untrusted, fence_untrusted_inline
from ..core.validation import validate_params

MAX_LIST_ITEMS = 30


def _origin() -> str:
    from ..core.config import get_config
    base = get_config().canvas_api_url
    return base.split("/api/")[0] if "/api/" in base else base.rstrip("/")


def _is_err(x: Any) -> bool:
    return isinstance(x, dict) and "error" in x


def _to_local(iso_utc: str | None) -> tuple[str | None, int | None]:
    """UTC ISO string -> (local ISO string, daysUntil). (None, None) if absent."""
    if not iso_utc:
        return None, None
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError:
        return None, None
    tz = local_tz()
    local = dt.astimezone(tz)
    return local.isoformat(timespec="minutes"), (local.date() - datetime.now(tz).date()).days


def _cap(items: list, label: str) -> tuple[list, str | None]:
    """Apply the ~30-item rule: truncate with an honest note."""
    if len(items) <= MAX_LIST_ITEMS:
        return items, None
    return (
        items[:MAX_LIST_ITEMS],
        f"showing {MAX_LIST_ITEMS} of {len(items)} {label}; narrow the range to see the rest",
    )


async def _resolve(course: str | int) -> tuple[str, str] | dict:
    resolved = await resolve_course(course)
    if resolved is None:
        return {"error": f"No course matches '{course}'. Try get_courses to see what exists."}
    return resolved


def _chunk(seq: list, n: int) -> list[list]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def register_surface_tools(mcp: FastMCP) -> None:
    """Register the eleven-tool HULMS surface."""

    # ------------------------------------------------------------------ agenda
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_agenda(
        days: int = 14, start: str | None = None, end: str | None = None
    ) -> dict:
        """Get everything due across all courses: assignments, quizzes,
        discussions, calendar events, planner notes. Use this for any
        "what's due / what's coming up" question. Times are local
        (Asia/Karachi); daysUntil is precomputed — do not recalculate dates.

        Args:
            days: How many days ahead to look (default 14). Ignored when
                start/end are given.
            start: Optional ISO date (YYYY-MM-DD) to start from.
            end: Optional ISO date (YYYY-MM-DD) to stop at (exclusive).
        """
        if days < 1:
            return {"error": "days must be at least 1"}
        tz = local_tz()
        try:
            start_utc = (
                datetime.fromisoformat(start).replace(tzinfo=tz).astimezone(timezone.utc)
                if start else datetime.now(timezone.utc)
            )
            end_utc = (
                datetime.fromisoformat(end).replace(tzinfo=tz).astimezone(timezone.utc)
                if end else start_utc + timedelta(days=days)
            )
        except ValueError:
            return {"error": "start/end must be ISO dates like 2026-09-01"}

        agenda = await collect_agenda(start_utc, end_utc)
        if _is_err(agenda):
            return agenda
        for entry in agenda:
            entry["title"] = fence_untrusted_inline(entry["title"], "item title")
        items, note = _cap(agenda, "agenda items")
        result = {"items": items, "count": len(agenda)}
        if note:
            result["note"] = note
        return result

    # ----------------------------------------------------------------- courses
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_courses(state: str = "active") -> dict:
        """List enrolled courses with current scores. Use this to find a
        course's exact name or id, or to answer "what am I taking / what did
        I take".

        Args:
            state: "active" (default), "completed" (past courses), or "all".
        """
        if state not in ("active", "completed", "all"):
            return {"error": 'state must be "active", "completed", or "all"'}

        wanted = ["active", "completed"] if state == "all" else [state]
        courses: list[dict] = []
        for enrollment_state in wanted:
            batch = await fetch_all_paginated_results(
                "/courses",
                {
                    "enrollment_state": enrollment_state,
                    "include[]": ["term", "total_scores"],
                    "per_page": 100,
                },
            )
            if _is_err(batch):
                return batch
            for c in batch:
                enrollments = c.get("enrollments") or []
                score = enrollments[0].get("computed_current_score") if enrollments else None
                courses.append({
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "code": c.get("course_code"),
                    "term": ((c.get("term") or {}).get("name") or "").strip(),
                    "state": enrollment_state,
                    "currentScore": score,
                })
        return {"courses": courses, "count": len(courses)}

    # -------------------------------------------------------------- assignment
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_assignment(course: str | int, assignment_id: str | int) -> dict:
        """Get one assignment in full: description as plain text, due date,
        points, and your submission status. Use after get_agenda or
        get_course_map surfaces an assignment worth reading.

        Args:
            course: Course name, code, or id.
            assignment_id: Canvas assignment id.
        """
        resolved = await _resolve(course)
        if _is_err(resolved):
            return resolved
        course_id, course_name = resolved

        a = await make_canvas_request(
            "get",
            f"/courses/{course_id}/assignments/{assignment_id}",
            params={"include[]": "submission"},
        )
        if _is_err(a):
            return a

        due_local, days_until = _to_local(a.get("due_at"))
        return {
            "course": course_name,
            "name": fence_untrusted_inline(a.get("name") or "Unnamed", "assignment name"),
            "due": due_local,
            "daysUntil": days_until,
            "points": a.get("points_possible"),
            "submissionTypes": a.get("submission_types") or [],
            "status": derive_status({"submissions": a.get("submission")}),
            "description": fence_untrusted(
                strip_html_tags(a.get("description") or "") or "(no description)",
                "assignment description",
            ),
            "url": absolute_url(a.get("html_url")),
        }

    # ----------------------------------------------------------- announcements
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_announcements(since_days: int = 14) -> dict:
        """Get recent announcements from every active course in one shot.
        Use for "did I miss anything / any updates from my courses".

        Args:
            since_days: How far back to look (default 14).
        """
        if since_days < 1:
            return {"error": "since_days must be at least 1"}

        active = await fetch_all_paginated_results(
            "/courses", {"enrollment_state": "active", "per_page": 100}
        )
        if _is_err(active):
            return active
        names = {f"course_{c['id']}": c.get("name", "?") for c in active if c.get("id")}
        if not names:
            return {"announcements": [], "count": 0, "note": "no active courses"}

        start = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%d")
        collected: list[dict] = []
        # context_codes is limited to 10 contexts per request — chunk, but it
        # stays ONE batched call per 10 courses, never one per course.
        for chunk in _chunk(sorted(names), 10):
            batch = await fetch_all_paginated_results(
                "/announcements",
                {"context_codes[]": chunk, "start_date": start, "per_page": 100},
            )
            if _is_err(batch):
                return batch
            collected.extend(batch)

        collected.sort(key=lambda a: a.get("posted_at") or "", reverse=True)
        shaped = []
        for ann in collected:
            posted_local, _ = _to_local(ann.get("posted_at"))
            shaped.append({
                "course": names.get(ann.get("context_code"), "?"),
                "title": fence_untrusted_inline(ann.get("title") or "Untitled", "announcement title"),
                "postedAt": posted_local,
                "body": fence_untrusted(
                    strip_html_tags(ann.get("message") or ""), "announcement body"
                ),
                "url": absolute_url(ann.get("html_url")),
            })
        items, note = _cap(shaped, "announcements")
        result = {"announcements": items, "count": len(shaped)}
        if note:
            result["note"] = note
        return result

    # ---------------------------------------------------------------- calendar
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_calendar(start: str, end: str) -> dict:
        """Get actual class meetings and events between two dates. Use for
        "when does X meet / what's on my schedule", NOT for deadlines —
        deadlines come from get_agenda.

        Args:
            start: ISO date (YYYY-MM-DD), inclusive.
            end: ISO date (YYYY-MM-DD), inclusive.
        """
        active = await fetch_all_paginated_results(
            "/courses", {"enrollment_state": "active", "per_page": 100}
        )
        if _is_err(active):
            return active
        names = {f"course_{c['id']}": c.get("name", "?") for c in active if c.get("id")}

        events: list[dict] = []
        for chunk in _chunk(sorted(names), 10):
            batch = await fetch_all_paginated_results(
                "/calendar_events",
                {
                    "type": "event",
                    "context_codes[]": chunk,
                    "start_date": start,
                    "end_date": end,
                    "per_page": 100,
                },
            )
            if _is_err(batch):
                return batch
            events.extend(batch)

        events.sort(key=lambda e: e.get("start_at") or "")
        shaped = []
        for ev in events:
            start_local, days_until = _to_local(ev.get("start_at"))
            end_local, _ = _to_local(ev.get("end_at"))
            shaped.append({
                "course": names.get(ev.get("context_code"), "?"),
                "title": fence_untrusted_inline(ev.get("title") or "Untitled", "event title"),
                "start": start_local,
                "end": end_local,
                "daysUntil": days_until,
                "location": fence_untrusted_inline(ev.get("location_name") or "", "location"),
                "url": absolute_url(ev.get("html_url")),
            })
        items, note = _cap(shaped, "events")
        result = {"events": items, "count": len(shaped)}
        if note:
            result["note"] = note
        return result

    # -------------------------------------------------------------------- todo
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_todo() -> dict:
        """Get the Canvas TODO list — includes ungraded quizzes that
        get_agenda can miss. Use alongside get_agenda when the question is
        "what do I still owe".
        """
        todos = await fetch_all_paginated_results("/users/self/todo", {"per_page": 100})
        if _is_err(todos):
            return todos

        shaped = []
        for item in todos:
            assignment = item.get("assignment") or {}
            due_local, days_until = _to_local(assignment.get("due_at"))
            shaped.append({
                "course": item.get("context_name") or "?",
                "type": item.get("type") or "item",
                "title": fence_untrusted_inline(
                    assignment.get("name") or item.get("title") or "Untitled", "todo title"
                ),
                "due": due_local,
                "daysUntil": days_until,
                "url": absolute_url(assignment.get("html_url") or item.get("html_url")),
            })
        return {"todo": shaped, "count": len(shaped)}

    # ------------------------------------------------------------ peer reviews
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_peer_reviews(course: str | int | None = None) -> dict:
        """Get peer reviews YOU still owe. Cheap and easy to forget — check
        this when the user asks what's outstanding, not only when they say
        "peer review".

        Args:
            course: Optional course name, code, or id to narrow the scan.
        """
        me = await make_canvas_request("get", "/users/self")
        if _is_err(me) or not me.get("id"):
            return {"error": f"Could not identify current user: {me.get('error', me)}"}
        my_id = me["id"]

        if course is not None:
            resolved = await _resolve(course)
            if _is_err(resolved):
                return resolved
            course_ids = [resolved[0]]
        else:
            active = await fetch_all_paginated_results(
                "/courses", {"enrollment_state": "active", "per_page": 100}
            )
            if _is_err(active):
                return active
            course_ids = [c["id"] for c in active if c.get("id")]

        pending: list[dict] = []
        unchecked: list[str] = []

        # Path 1: assignment-scoped scan (peer_reviews flag on the listing).
        for cid in course_ids:
            assignments = await fetch_all_paginated_results(
                f"/courses/{cid}/assignments", {"per_page": 100}
            )
            if _is_err(assignments):
                unchecked.append(f"course {cid}: {assignments['error']}")
                continue
            for a in assignments:
                if not a.get("peer_reviews"):
                    continue
                reviews = await fetch_all_paginated_results(
                    f"/courses/{cid}/assignments/{a['id']}/peer_reviews", {"per_page": 100}
                )
                if _is_err(reviews):
                    unchecked.append(f"{a.get('name', a['id'])}: {reviews['error']}")
                    continue
                for r in reviews:
                    if r.get("assessor_id") == my_id and r.get("workflow_state") != "completed":
                        pending.append({
                            "course": str(cid),
                            "assignment": fence_untrusted_inline(
                                a.get("name") or "Unnamed", "assignment name"
                            ),
                            "reviewId": r.get("id"),
                            "source": "assignment scan",
                        })

        # Path 2: the Planner feed — Canvas's own student To-Do builds from it,
        # and it catches reviews the flag-based scan misses (upstream #275,
        # live-verified fields). Union, not replacement.
        planner = await fetch_all_paginated_results(
            "/planner/items", {"filter": "incomplete_items", "per_page": 100}
        )
        if _is_err(planner):
            unchecked.append(f"planner feed: {planner['error']}")
        else:
            seen = {(p["course"], str(p["reviewId"])) for p in pending if p.get("reviewId")}
            for item in planner:
                if item.get("plannable_type") != "assessment_request":
                    continue
                plannable = item.get("plannable") or {}
                if plannable.get("workflow_state") == "completed":
                    continue
                assessor = plannable.get("assessor_id")
                if assessor is not None and assessor != my_id:
                    continue
                cid = str(item.get("course_id"))
                if course is not None and cid not in [str(c) for c in course_ids]:
                    continue
                rid = str(plannable.get("id") or item.get("plannable_id"))
                if (cid, rid) in seen:
                    continue
                pending.append({
                    "course": item.get("context_name") or cid,
                    "assignment": fence_untrusted_inline(
                        plannable.get("title") or "Unnamed", "assignment name"
                    ),
                    "reviewId": rid,
                    "source": "planner feed",
                })

        result: dict[str, Any] = {"pending": pending, "count": len(pending)}
        if unchecked:
            # "Nothing owed" is only a safe answer when every listing succeeded.
            result["coverage"] = (
                "some listings could not be checked; reviews may still exist: "
                + "; ".join(unchecked)
            )
        return result

    # ---------------------------------------------------------------- syllabus
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_syllabus(
        course: str | int, format: str = "text", max_chars: int | None = None
    ) -> dict:
        """Get the course syllabus — the highest-information document in any
        course: grading breakdown, weekly topics, policies, exam dates. Use
        it before answering anything about how a course is graded or
        structured.

        Args:
            course: Course name, code, or id.
            format: "text" (plain text, default) or "html" (raw body).
            max_chars: Optional cap; content beyond it is truncated with a marker.
        """
        if format not in ("text", "html"):
            return {"error": 'format must be "text" or "html"'}
        if max_chars is not None and max_chars <= 0:
            return {"error": "max_chars must be positive"}

        resolved = await _resolve(course)
        if _is_err(resolved):
            return resolved
        course_id, course_name = resolved

        syl = await resolve_syllabus(course_id)
        if syl["source"] == "none":
            return {
                "source": "none",
                "course": course_name,
                "text": "",
                "url": absolute_url(syl["url"]) if syl["url"] else f"{_origin()}/courses/{course_id}/assignments/syllabus",
                "note": syl.get("note", ""),
            }

        text = syl["text"]
        if format == "html":
            if "html" in syl:
                text = syl["html"]
            else:
                # File-sourced syllabi have no HTML form; say so, return text.
                pass
        truncated = max_chars is not None and len(text) > max_chars
        if truncated:
            text = text[:max_chars] + f"\n...[truncated at {max_chars} characters]"
        result = {
            "source": syl["source"],
            "course": course_name,
            "text": fence_untrusted(text, "course syllabus"),
            "url": absolute_url(syl["url"]),
        }
        if syl["source"] == "file":
            result["file"] = fence_untrusted_inline(syl.get("name") or "syllabus file", "file name")
            if format == "html":
                result["note"] = "syllabus came from a file; only plain text is available"
        if truncated:
            result["note"] = f"truncated at {max_chars} characters; call again without max_chars for all of it"
        return result

    # ----------------------------------------------------------- grade weights
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_grade_weights(course: str | int) -> dict:
        """Get the real grade breakdown for a course. Use this instead of
        weighting by assignment points — points_possible is frequently 0
        at this institution and misleads.

        Args:
            course: Course name, code, or id.
        """
        resolved = await _resolve(course)
        if _is_err(resolved):
            return resolved
        course_id, course_name = resolved

        groups = await fetch_all_paginated_results(
            f"/courses/{course_id}/assignment_groups", {"per_page": 100}
        )
        if _is_err(groups):
            return groups

        weights = [
            {
                "name": fence_untrusted_inline(g.get("name") or "Unnamed", "group name"),
                "percent": g.get("group_weight"),
            }
            for g in groups
            if (g.get("group_weight") or 0) > 0
        ]
        if weights:
            return {"source": "groups", "course": course_name, "weights": weights}

        # Fallback: recover the breakdown from the syllabus (field or file).
        syl = await resolve_syllabus(course_id)
        if syl["source"] == "field" and not parse_weights(syl["text"]):
            # The field can be an intro blurb while the real syllabus (with
            # the breakdown) is an uploaded PDF — measured live. Retry on file.
            file_syl = await resolve_syllabus(course_id, skip_field=True)
            if file_syl["source"] == "file" and parse_weights(file_syl["text"]):
                syl = file_syl
        if syl["source"] != "none" and syl["text"]:
            parsed = parse_weights(syl["text"])
            if parsed:
                return {
                    "source": "syllabus",
                    "course": course_name,
                    "weights": [
                        {
                            "name": fence_untrusted_inline(w["name"], "component name"),
                            "percent": w["percent"],
                        }
                        for w in parsed
                    ],
                    "url": absolute_url(syl["url"]),
                    "note": (
                        f"Parsed from the syllabus ({syl['source']}"
                        + (f": {syl.get('name')}" if syl.get("name") else "")
                        + "); verify against the source if a grade depends on it."
                    ),
                }
            return {
                "source": "none",
                "course": course_name,
                "weights": [],
                "url": absolute_url(syl["url"]),
                "note": (
                    "No weighted assignment groups, and the syllabus was found "
                    "but its breakdown could not be parsed with confidence. "
                    "Call get_syllabus and read the grading section directly."
                ),
            }
        return {
            "source": "none",
            "course": course_name,
            "weights": [],
            "note": (
                "No weighted assignment groups and no syllabus found. "
                + syl.get("note", "")
            ),
        }

    # -------------------------------------------------------------- course map
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_course_map(course: str | int) -> dict:
        """Get the structural view of a whole course: every module with its
        items, types, and dates. Use this to answer "what did week/module N
        cover", to find the material behind a quiz, or to orient in an
        unfamiliar course.

        Args:
            course: Course name, code, or id.
        """
        resolved = await _resolve(course)
        if _is_err(resolved):
            return resolved
        course_id, course_name = resolved

        mods = await fetch_all_paginated_results(
            f"/courses/{course_id}/modules", {"include[]": "items", "per_page": 100}
        )
        if _is_err(mods):
            return mods

        modules = []
        total_items = 0
        for m in mods:
            items = []
            for i in m.get("items") or []:
                due_local, _ = _to_local((i.get("content_details") or {}).get("due_at"))
                entry = {
                    "title": fence_untrusted_inline(i.get("title") or "Untitled", "item title"),
                    "type": i.get("type"),
                    "url": absolute_url(i.get("html_url")),
                }
                if i.get("type") == "File" and i.get("content_id"):
                    entry["fileId"] = i.get("content_id")
                if due_local:
                    entry["due"] = due_local
                items.append(entry)
            total_items += len(items)
            modules.append({
                "module": fence_untrusted_inline(m.get("name") or "Unnamed", "module name"),
                "position": m.get("position"),
                "itemCount": len(items),
                "items": items,
            })

        return {
            "course": course_name,
            "url": f"{_origin()}/courses/{course_id}/modules",
            "moduleCount": len(modules),
            "itemCount": total_items,
            "modules": modules,
        }

    # ------------------------------------------------------------ planner note
    @mcp.tool()
    @validate_params
    async def create_planner_note(
        title: str, date: str, course: str | int | None = None, details: str | None = None
    ) -> dict:
        """Create a planner note in Canvas — the one write this server does.
        Use it to put a plan back where the user will see it: on their Canvas
        dashboard for the given date.

        Args:
            title: Short note title.
            date: ISO date (YYYY-MM-DD) the note belongs to.
            course: Optional course name, code, or id to attach the note to.
            details: Optional longer note body.
        """
        try:
            datetime.fromisoformat(date)
        except ValueError:
            return {"error": "date must be an ISO date like 2026-09-01"}

        payload: dict[str, Any] = {"title": title, "todo_date": date}
        if details:
            payload["details"] = details
        if course is not None:
            resolved = await _resolve(course)
            if _is_err(resolved):
                return resolved
            payload["course_id"] = resolved[0]

        created = await make_canvas_request("post", "/planner_notes", data=payload)
        if _is_err(created):
            return created
        return {
            "created": True,
            "id": created.get("id"),
            "title": created.get("title"),
            "todoDate": created.get("todo_date"),
            "course_id": created.get("course_id"),
            "url": f"{_origin()}/calendar",
        }
