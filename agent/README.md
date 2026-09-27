# agent

The Canvas MCP server and its command-line tools. Single user, local only, stdio transport. Points at any Canvas host via `.env`. The [root README](../README.md) covers the whole system; this one is about running and understanding the server itself.

Forked from [vishalsachdev/canvas-mcp](https://github.com/vishalsachdev/canvas-mcp) (MIT, retained in `LICENSE`). The educator, hosted-deployment and FERPA-anonymization surfaces were removed; the Canvas plumbing and the student tool surface were kept. Git history back to the fork point is intact.

### Install and verify

1. Create a virtual environment here and install the package with the dev extras (pytest, ruff).

```bash
python -m venv .venv
.venv\Scripts\pip install -e . --group dev     # Windows
```

```bash
python3 -m venv .venv
.venv/bin/pip install -e . --group dev         # macOS / Linux
```

2. Create `.env` in this directory.

```
CANVAS_HOST=https://your-school.instructure.com
CANVAS_TOKEN=<your token>
TIMEZONE=Europe/London
```

3. Check the connection.

```bash
canvas-server --test
```

```
✓ API connection successful! Authenticated as: Your Name
```

4. Run the test suite. It should be entirely green; a red test is a bug, not noise.

```bash
python -m pytest -q
ruff check .
```

> [!NOTE]
> All commands below assume the venv's `Scripts` (Windows) or `bin` (macOS) directory is on your PATH, or prefix them with `.venv\Scripts\` / `.venv/bin/`.

### Optional environment

Same `.env` file, all optional:

- `CANVAS_SPACES_DIR` — where the per-course spaces live. Default `~/CanvasCoach/spaces`. Must match the UI, which reads the same variable.
- `CANVAS_DB` — path of the SQLite cache. Default `canvas.db` in this directory.
- `CANVAS_DISABLE_TOOLS` — tool names to withhold at registration, comma-separated. Set by a client per launch (the UI's Codex backend uses it to drop `fetch_web_image`), not normally by hand.
- `STUDENT_WRITE_TOOLS` — opt in to write tools beyond `create_planner_note`, such as `submit_assignment`. Off by default.

### The command-line tools

Every CLI shares the server's client, cache and timezone handling, so anything they print is exactly what the model would see.

1. **Audit a course**, or every completed course, to learn what Canvas actually holds: modules, assignments with real due dates, files by extension, pages, syllabus presence, group weights.

```bash
canvas-audit                    # every completed course, one summary table
canvas-audit "Data Struct"      # one course (id, code, or name substring), detailed
canvas-audit --fresh            # bypass the cache
```

2. **Print the agenda** for the next N days, in your timezone, with a status per item derived from submissions.

```bash
canvas-agenda 14
```

3. **Index a course's files** for search. Cached on each file's `updated_at`, so re-runs only download what changed.

```bash
canvas-extract "Data Structures"
canvas-extract 4361 --modules-only     # only files placed in modules
```

4. **Generate today's brief.** Re-indexes active courses, then writes the digest the calendar feed serves as an all-day event. Exits non-zero if any section could not be fetched, and says so in the brief instead of claiming all quiet.

```bash
canvas-brief --force
```

5. **Back up** the database and the spaces folder into one timestamped zip. The token is never included. Keeps the newest ten.

```bash
canvas-backup                   # to <repo>/backups/
canvas-backup D:\usb            # anywhere else
canvas-backup --keep 20
```

6. **Emit the calendar** as iCalendar text, which the UI serves at `/api/calendar.ics`.

```bash
canvas-ics
```

7. **Show the resolved configuration** without the token.

```bash
canvas-server --config
```

### The tool surface

29 tools, all over stdio MCP, registered under the server name `canvas`. Every `course` parameter accepts a name, code or id and is resolved against the cached course list. Canvas-authored text arrives fenced as untrusted content, `daysUntil` is computed server-side, and every item carries an absolute link.

1. **The core eleven**, per the build brief: `get_agenda`, `get_courses`, `get_assignment`, `get_announcements`, `get_calendar`, `get_todo`, `get_peer_reviews`, `get_syllabus`, `get_grade_weights`, `get_course_map`, and the one write, `create_planner_note`. Plus `get_my_submission` from the env-gated student-write module.

2. **Study mode**: `get_study_context` walks from a quiz, assignment, module or date to its module and returns every item with the file text already extracted; it is the workhorse. `get_file_text`, `search_course_content` (SQLite FTS5, for questions that cross modules), `get_announcement_context`, `index_course_files` and `read_local_document` (files dropped into a space) round it out. Extraction covers PDF (scanned PDFs are detected and skipped), PPTX with speaker notes, DOCX with tables, notebooks, HTML and plain text.

> [!NOTE]
> `include[]=items` on the modules endpoint silently omits the items array for large modules. `core/modules.py` fetches those separately, so the biggest modules never read as empty.

3. **Syllabus and weights**: `get_syllabus` resolves the Canvas field, then a name-matched syllabus file, then reports `none`. `get_grade_weights` prefers assignment-group weights, then parses the syllabus conservatively (end-anchored percentage lines, prose and grade-scale rows excluded, accepted only when components sum to about 100), then returns an honest `none`.

4. **Grades**: `get_my_grades` gives every graded item by group and a weighted current score computed here, with a discrepancy flag when Canvas's number differs. Assignments marked `omit_from_final_grade` are excluded.

5. **Retrieval practice**: `log_retrieval_item`, `get_due_reviews`, `record_review_result`. Leitner boxes on a 1/3/7/14/30 day schedule, dated in the configured timezone.

6. **Planning**: `add_plan_event`, `list_plan_events`, `delete_plan_event`. These land on the calendar feed.

7. **Figures**: `get_document_images`, `render_document_pages` (for vector line-art the image extractor cannot see), `crop_image`, and `fetch_web_image`, which refuses non-public addresses on every redirect hop and stops downloading at the size cap.

> [!TIP]
> Tool descriptions are the prompt engineering. Each one says when to use it, not only what it returns. If the model reaches for the wrong tool, fix the description before touching the system prompt.
