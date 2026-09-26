# agent

Personal Canvas MCP server — single user, local only, stdio transport.
Points at any Canvas host via `.env`. See the repository root README for the full system.

## Provenance

Forked from [vishalsachdev/canvas-mcp](https://github.com/vishalsachdev/canvas-mcp)
(MIT license — retained in `LICENSE`). The educator, hosted-deployment, and
FERPA-anonymization surfaces were removed; the Canvas plumbing and the student
tool surface were kept. Git history back to the fork point is intact.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -e . --group dev    # Windows
.venv/bin/pip install -e . --group dev         # macOS / Linux
```

`.env` (gitignored) in this directory:

```
CANVAS_HOST=https://your-school.instructure.com
CANVAS_TOKEN=<your token>
TIMEZONE=Europe/London
```

## Commands

- Start server: `canvas-server` (stdio MCP; registered in Claude Desktop as `canvas`)
- Test connection: `canvas-server --test`
- Show config: `canvas-server --config`
- Corpus audit: `canvas-audit` (all completed courses) / `canvas-audit "Data Struct"`
- Agenda: `canvas-agenda [days]`
- Bulk text extraction: `canvas-extract "Data Struct"` (indexes a course for search)
- Daily brief: `canvas-brief` (scheduled 07:30 + at logon; lands on the phone calendar)
- Backup: `canvas-backup [dest]` (canvas.db + spaces/, never the token; weekly Sun 20:00; keeps last 10)
- Run tests: `python -m pytest tests/`

## Tool surface (step 2)

Eleven tools, per the build brief: `get_agenda`, `get_courses`,
`get_assignment`, `get_announcements`, `get_calendar`, `get_todo`,
`get_peer_reviews`, `get_syllabus`, `get_grade_weights`, `get_course_map`,
and the one write, `create_planner_note`. Plus `get_my_submission`
(read-only) from the env-gated student-write module. Every `course`
parameter accepts a name, code, or id. Canvas-authored text arrives fenced
as untrusted content; `daysUntil` is computed server-side; every item
carries an absolute link.

## Study mode (step 3)

Four more tools: `get_study_context` (walk from a quiz/assignment/module/date
to its module and return every item with file text already extracted — the
workhorse), `get_file_text`, `search_course_content` (SQLite FTS5, for
cross-module questions only), and `get_announcement_context`. Extraction
covers PDF (with scanned-PDF detection and skip), PPTX (incl. speaker
notes), DOCX (incl. tables), notebooks, HTML, and plain text, cached in
SQLite keyed on each file's `updated_at`.

## Syllabus & weights (step 4)

`get_syllabus` resolves field -> file (name-matched, extracted via step 3;
module-walk fallback when the files listing is blocked) -> none. 
`get_grade_weights` resolves assignment-group weights -> syllabus parsing
(conservative: end-anchored percentage lines, prose and grade-scale rows
excluded, accepted only when components sum to ~100) -> an honest "none"
that points at the syllabus. When the field syllabus parses to nothing it
retries against a syllabus file, because the field is often just an intro
blurb.
