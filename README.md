# Canvas Coach

A personal AI study assistant for Canvas LMS. One student, one machine,
no cloud: a local MCP server translates Canvas into tools, and Claude does
the reasoning — through Claude Desktop or the bundled chat UI. Works
with any Canvas host.

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
agent/     Python MCP server (fork of vishalsachdev/canvas-mcp, MIT)
           ~20 tools over stdio · SQLite cache + FTS5 · CLIs:
           canvas-audit, canvas-agenda, canvas-extract, canvas-brief,
           canvas-ics, canvas-backup, canvas-courses
ui/        Next.js chat UI on 127.0.0.1:3117 · spawns `claude -p`
           per message (stream-json over SSE) · calendar feed at
           /api/calendar.ics, re-served to the phone by feed-proxy.mjs
           on LAN port 3118 (the UI itself is loopback-only)
~/CanvasCoach/spaces/   per-course working dirs: memory, plans, dropped
           files, extracted figures (personal data; outside the repo on
           purpose — override with CANVAS_SPACES_DIR)
```

Canvas-authored text is fenced as untrusted content before it reaches the
model (prompt-injection boundary, inherited from upstream and CI-enforced).
Date arithmetic and grade math happen in the tools, never in the model.

## Requirements

- Windows or macOS (launchers and scheduling are per-platform; see Setup)
- Python 3.10+, Node.js 22+
- [Claude Code](https://claude.com/claude-code) installed and signed in
  (subscription auth; no API key is used anywhere)
- A Canvas personal access token (Canvas → Account → Settings → New Access
  Token). **This token can read your grades and submit work — treat it
  like a password.**

## Setup

Create `agent/.env` (gitignored) on either platform:

```
CANVAS_HOST=https://your-school.instructure.com
CANVAS_TOKEN=your-token-here
TIMEZONE=Europe/London
```

Then, per platform (all paths relative to the repo root):

| | Windows | macOS |
|---|---|---|
| MCP server | `cd agent`, `python -m venv .venv`, `.venv\Scripts\pip install -e .` | `cd agent`, `python3 -m venv .venv`, `.venv/bin/pip install -e .` |
| Verify | `.venv\Scripts\canvas-server --test` prints your Canvas name | `.venv/bin/canvas-server --test` prints your Canvas name |
| Chat UI | `cd ui`, `npm install`, then `dev.cmd` (dev) or `start.cmd` (built, faster) | `cd ui`, `npm install`, then `./dev.sh` or `./start.sh` |
| Claude Desktop | add `agent\.venv\Scripts\canvas-server.exe` as MCP server `canvas` in `%APPDATA%\Claude\claude_desktop_config.json` | add `agent/.venv/bin/canvas-server` as MCP server `canvas` in `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Daily brief + weekly backup | Task Scheduler: `canvas-brief.exe --force` daily 07:30, `canvas-backup.exe` Sun 20:00, plus an at-logon `canvas-brief.exe` (skips if today's exists) | `ui/launchd/` plists, same times; launchd replays a run missed during sleep on wake, so no at-logon job (see `launchd/README.md`) |
| Phone calendar | allow inbound TCP 3118 in Windows Firewall (Private profile) | the macOS firewall, if on, prompts once for `node`; allow it |

macOS gotcha: if the repo lives in an iCloud-synced folder (Desktop or
Documents), iCloud marks dot-folders hidden and Python then ignores the
venv's `.pth` file, so `import canvas_mcp` fails. Either keep the repo
outside iCloud, or create the venv as `venv.nosync` and symlink `.venv` to
it (`ln -s venv.nosync .venv`). Also keep `canvas.db` out of any synced
folder; two syncing writers corrupt SQLite.

Either UI launcher opens http://localhost:3117 and starts the feed proxy.
For the phone, subscribe (same Wi-Fi) to
`http://<this-machine's-lan-ip>:3118/api/calendar.ics` as a
subscribed calendar, choosing "continue without SSL" when iOS asks.
`canvas-backup` zips the database and spaces (never the token); pass a
directory to target a USB/synced folder.

## What is exposed, and what the model can touch

- The UI binds to 127.0.0.1. The only thing on the LAN is `feed-proxy.mjs`,
  which forwards one GET path (the calendar) to the UI and 404s everything
  else. Nobody on the Wi-Fi can reach the chat, uploads, or memory files.
- Spaces live outside the repository (`~/CanvasCoach/spaces`), so the
  coach's working directory has no `CLAUDE.md` above it and no project
  tree beside it.
- `claude -p` runs with its file tools confined by permission rules to the
  course's space folder plus the shared figure store, with explicit denies
  on the files Claude Code reads as instructions or settings (`CLAUDE.md`,
  `.claude/`, `.mcp.json`). Uploads refuse those names too. Shell is never
  allowed. Canvas-authored text (bodies, titles, file names, notes) is fenced
  as untrusted before the model sees it.
- Web fetches from the model refuse non-public addresses on every redirect
  hop and stop downloading at the size cap.

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
(MIT — license retained in `agent/LICENSE`), stripped from its
~90-tool multi-persona hosted design to a single-user student surface, with
study mode, extraction, syllabus recovery, spaced retrieval, grades, and
the calendar pipeline added on top.
