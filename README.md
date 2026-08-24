# HULMS Assistant

A personal AI study assistant for Canvas LMS. One student, one machine,
no cloud: a local MCP server translates Canvas into tools, and Claude does
the reasoning — through Claude Desktop or the bundled chat UI. Built for
Habib University's Canvas instance, but it points at any Canvas host.

**The design premise:** tools fetch, the model reasons. There is no agent
loop, no Anthropic API key, and no summarizer. The intelligence comes from
Claude Code's subscription auth; this project's whole job is fetching Canvas
data and shaping it honestly.

## What it does

- **Ask anything about your courses** — deadlines, announcements, course
  structure, grades — grounded in live Canvas data with a source link on
  every claim. Missing data is reported as missing, never guessed.
- **Study mode** — point it at a quiz, module, or date and it walks to the
  containing module and reads the actual material: PDF, PPTX (with speaker
  notes), DOCX, notebooks — extracted in-process, cached in SQLite, indexed
  with FTS5 for cross-week search. Scanned PDFs are detected and skipped,
  never half-read.
- **Retrieval practice, not summaries** — the coach quizzes you from the
  real material; every miss is logged and resurfaces on a spaced schedule
  (1/3/7/14/30 days, Leitner-style) at the start of later sessions.
- **Syllabus recovery** — when Canvas's syllabus field is empty (common:
  the real syllabus is a PDF, sometimes behind Simple Syllabus SSO), it
  finds and extracts syllabus files, and conservatively parses the grade
  breakdown — accepted only when the components sum to ~100%.
- **Grade standing** — every graded item by assignment group, the current
  weighted score computed server-side, and honest discrepancy flags when
  Canvas's number differs (instructors drop lowest scores; the API can't
  see that). What-if arithmetic is done in the open, from that structure.
- **Planning that reaches your phone** — study sessions land on an
  iCalendar feed your phone subscribes to over Wi-Fi, merged with Canvas
  deadlines. A daily brief (overnight announcements, the week's deadlines,
  newly posted files, reviews due) arrives as an all-day calendar event.
- **Chat UI with course spaces** — each course gets its own conversations,
  persistent `memory.md` and `plan.md` (maintained by the coach, editable
  by you), and a folder for dropped files, which become readable and
  searchable.

## Architecture

```
hulms-agent/   Python MCP server (fork of vishalsachdev/canvas-mcp, MIT)
               ~20 tools over stdio · SQLite cache + FTS5 · CLIs:
               hulms-audit, hulms-agenda, hulms-extract, hulms-brief,
               hulms-ics, hulms-backup, hulms-courses
hulms-ui/      Next.js chat UI on localhost:3117 · spawns `claude -p`
               per message (stream-json over SSE) · serves the calendar
               feed at /api/hulms.ics
spaces/        per-course working dirs: memory, plans, dropped files
               (personal data — gitignored)
```

Canvas-authored text is fenced as untrusted content before it reaches the
model (prompt-injection boundary, inherited from upstream and CI-enforced).
Date arithmetic and grade math happen in the tools, never in the model.

## Requirements

- Windows (paths, Task Scheduler, and launch scripts are Windows-specific)
- Python 3.10+, Node.js 22+
- [Claude Code](https://claude.com/claude-code) installed and signed in
  (subscription auth; no API key is used anywhere)
- A Canvas personal access token (Canvas → Account → Settings → New Access
  Token). **This token can read your grades and submit work — treat it
  like a password.**

## Setup

```bash
# 1. The MCP server
cd hulms-agent
python -m venv .venv
.venv\Scripts\pip install -e .
```

Create `hulms-agent/.env`:

```
CANVAS_HOST=https://your-school.instructure.com
CANVAS_TOKEN=your-token-here
TIMEZONE=Asia/Karachi
```

```bash
# 2. Verify
.venv\Scripts\hulms-server --test     # should print your Canvas name

# 3. The chat UI
cd ../hulms-ui
npm install
dev.cmd                               # starts on http://localhost:3117 and opens the browser
```

Optional, recommended:

- **Claude Desktop**: add `hulms-agent\.venv\Scripts\hulms-server.exe` as an
  MCP server named `hulms` in `claude_desktop_config.json`.
- **Phone calendar**: allow inbound TCP 3117 in Windows Firewall (Private
  profile), then subscribe your phone (same Wi-Fi) to
  `http://<pc-lan-ip>:3117/api/hulms.ics` as a subscribed calendar
  (choose "continue without SSL" when iOS asks).
- **Daily brief**: schedule `hulms-brief.exe --force` daily and/or drop a
  Startup-folder script for at-logon runs (skips if today's brief exists).
- **Weekly backup**: schedule `hulms-backup.exe` — zips the database and
  spaces (never the token); pass a directory to target a USB/synced folder.

## Honest limitations

- The tools know only what is in Canvas. Deadlines moved verbally or on
  WhatsApp are invisible.
- Scanned (image-only) PDFs are skipped rather than half-OCRed.
- Simple Syllabus and other SSO-gated LTI tools are unreachable — export
  the PDF and drop it into the course's space folder instead.
- The calendar feed updates only while the UI server runs and the phone is
  on the same network; phones keep showing the last fetched copy offline.

## Credits and license

The MCP server is a fork of
[vishalsachdev/canvas-mcp](https://github.com/vishalsachdev/canvas-mcp)
(MIT — license retained in `hulms-agent/LICENSE`), stripped from its
~90-tool multi-persona hosted design to a single-user student surface, with
study mode, extraction, syllabus recovery, spaced retrieval, grades, and
the calendar pipeline added on top.
