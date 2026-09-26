"""Course caching and identifier resolution for Canvas API."""

from .client import fetch_all_paginated_results, make_canvas_request
from .logging import log_error, log_info
from .validation import validate_params

# Global cache for course codes to IDs
course_code_to_id_cache: dict[str, str] = {}
id_to_course_code_cache: dict[str, str] = {}
# (id, name, code) for every enrollment, active AND completed, newest first.
# Backs name-based resolution ("Discrete Math" -> 4299).
course_directory: list[tuple[str, str, str]] = []


async def refresh_course_cache() -> bool:
    """Refresh the global course cache.

    Fetches active and completed enrollments: past courses are the development
    corpus and must resolve by name just like current ones.
    """
    global course_code_to_id_cache, id_to_course_code_cache, course_directory

    log_info("Refreshing course cache")
    active = await fetch_all_paginated_results("/courses", {"per_page": 100})
    if isinstance(active, dict) and "error" in active:
        log_error("Error building course cache", error=active.get("error"))
        return False

    completed = await fetch_all_paginated_results(
        "/courses", {"enrollment_state": "completed", "per_page": 100}
    )
    if isinstance(completed, dict) and "error" in completed:
        # Degrade to active-only rather than failing the whole cache.
        log_error("Completed courses unavailable for cache", error=completed.get("error"))
        completed = []

    # Build caches for bidirectional lookups
    course_code_to_id_cache = {}
    id_to_course_code_cache = {}
    course_directory = []
    seen: set[str] = set()

    for course in list(active) + list(completed):
        course_id = str(course.get("id"))
        course_code = course.get("course_code")
        name = course.get("name") or ""

        if course_id in seen:
            continue
        seen.add(course_id)

        if course_code and course_id:
            course_code_to_id_cache[course_code] = course_id
            id_to_course_code_cache[course_id] = course_code
        if course_id:
            course_directory.append((course_id, name, course_code or ""))

    # Newest first: Canvas ids increase over time, so on an ambiguous name
    # match the most recent offering wins.
    course_directory.sort(key=lambda t: int(t[0]) if t[0].isdigit() else 0, reverse=True)

    log_info(f"Cached {len(course_directory)} courses")
    return True


async def resolve_course(identifier: str | int) -> tuple[str, str] | None:
    """Resolve a course name, code, or id to (course_id, display_name).

    Resolution order: numeric id -> exact code -> case-insensitive substring
    over names and codes (most recent offering wins on ambiguity). Returns
    None when nothing matches — callers should surface that rather than guess.
    """
    ident = str(identifier).strip()
    if not ident:
        return None

    if not course_directory:
        await refresh_course_cache()

    if ident.isdigit():
        for cid, name, _code in course_directory:
            if cid == ident:
                return cid, name
        # An id we don't know about is still a valid id (e.g. a section id
        # outside the enrollment list); pass it through.
        return ident, ident

    lowered = ident.lower()
    exact = [t for t in course_directory if t[2].lower() == lowered]
    if exact:
        cid, name, _code = exact[0]
        return cid, name

    matches = [
        t for t in course_directory
        if lowered in t[1].lower() or lowered in t[2].lower()
    ]
    if matches:
        cid, name, _code = matches[0]  # newest first
        return cid, name
    return None


@validate_params
async def get_course_id(course_identifier: str | int) -> str:
    """Get course ID from either course code or ID, with caching.

    Args:
        course_identifier: The course identifier, which can be:
                          - A course code (e.g., 'badm_554_120251_246794')
                          - A numeric course ID (as string or int)
                          - A SIS ID format (e.g., 'sis_course_id:xxx')

    Returns:
        The course ID as a string
    """
    global course_code_to_id_cache, id_to_course_code_cache

    # Convert to string for consistent handling
    course_str = str(course_identifier)

    # If it looks like a numeric ID
    if course_str.isdigit():
        return course_str

    # If it's a SIS ID format
    if course_str.startswith("sis_course_id:"):
        return course_str

    # If it's in our cache, return the ID
    if course_str in course_code_to_id_cache:
        return course_code_to_id_cache[course_str]

    # If it looks like a course code (contains underscores)
    if "_" in course_str:
        # Try to refresh cache if it's not there
        if not course_code_to_id_cache:
            await refresh_course_cache()
            if course_str in course_code_to_id_cache:
                return course_code_to_id_cache[course_str]

        # Return SIS format as a fallback
        return f"sis_course_id:{course_str}"

    # Last resort, return as is
    return course_str


async def get_course_code(course_id: str | int) -> str | None:
    """Get course code from ID, with caching."""
    global id_to_course_code_cache, course_code_to_id_cache

    course_id = str(course_id)

    # If it's already a code-like string with underscores
    if "_" in course_id:
        return course_id

    # If it's in our cache, return the code
    if course_id in id_to_course_code_cache:
        return id_to_course_code_cache[course_id]

    # Try to refresh cache if it's not there
    if not id_to_course_code_cache:
        await refresh_course_cache()
        if course_id in id_to_course_code_cache:
            return id_to_course_code_cache[course_id]

    # If we can't find a code, try to fetch the course directly
    response = await make_canvas_request("get", f"/courses/{course_id}")
    if "error" not in response and "course_code" in response:
        code: str | None = response.get("course_code", "")
        # Update our cache
        if code:
            id_to_course_code_cache[course_id] = code
            course_code_to_id_cache[code] = course_id
        return code

    # Last resort, return the ID
    return course_id
