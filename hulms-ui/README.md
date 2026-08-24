# hulms-ui

Local chat UI for the HULMS assistant (build brief step 5). One Next.js page
on localhost:3117; an API route spawns `claude -p` per message and streams
its output to the browser. No agent loop, no API key — Claude Code's
subscription auth does the work, and all Canvas access goes through the
hulms MCP server.

## Run

Double-click `dev.cmd` (or run it from a terminal). It starts the dev server
and opens http://localhost:3117.

## How it's put together

- **Course spaces**: every course (plus "General") is a space — a directory
  under `../spaces/<id>/` that is the working directory of the spawned
  `claude -p`. Conversations resume per space via `--resume <session_id>`.
- **Memory / planning**: each space holds `memory.md` and `plan.md`. The
  coach (see `coach.md`) reads and updates them with its file tools; the UI
  shows and lets you edit them directly (memory / plan buttons). Drop other
  files (e.g. a Simple Syllabus PDF export) into a space folder and the
  coach can read them.
- **Streaming**: `--output-format stream-json --include-partial-messages`,
  forwarded line-by-line as SSE; the page renders text deltas and tool-use
  chips.

## The three traps (from the build brief — do not regress)

1. Never pass `--bare`: it skips subscription auth and demands an API key.
   Claude Code is at 2.1.186 today; re-test after upgrades in case `-p`
   defaults change.
2. `ANTHROPIC_API_KEY` is deleted from the child environment in
   `app/api/chat/route.js` — if it exists, Claude Code prefers it over the
   subscription.
3. `-p` starts in Manual permission mode: every needed tool must be in
   `--allowedTools` (see `lib/spaces.js`) or the run blocks forever.

## Notes

- `../spaces/` is personal data (memory, plans, dropped files) and is not
  under version control anywhere.
- The MCP config (`hulms-mcp.local.json`) is generated at runtime with the
  resolved server path — machine-specific, gitignored.
