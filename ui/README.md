# ui

Local chat UI for Canvas Coach (build brief step 5). One Next.js page
on localhost:3117; an API route spawns `claude -p` per message and streams
its output to the browser. No agent loop, no API key — Claude Code's
subscription auth does the work, and all Canvas access goes through the
canvas MCP server.

## Run

Windows: double-click `dev.cmd` (or run it from a terminal). macOS:
`./dev.sh`. Either starts the dev server and opens http://localhost:3117.
`start.cmd` / `./start.sh` build once and serve the production bundle,
which is noticeably snappier; use the dev launcher only when changing UI
code. macOS scheduling lives in `launchd/`.

## How it's put together

- **Course spaces**: every course (plus "General") is a space — a directory
  under `~/CanvasCoach/spaces/<id>/` (or `CANVAS_SPACES_DIR`) that is the working directory of the spawned
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

## Backends

`lib/backends/<name>.js` is the whole contract with a CLI: how to build its
command line, how to translate its output lines into `init` / `text` /
`tool` / `result` events, and which models it offers. `app/api/backends`
reports which are installed. Add a CLI by adding a module and listing it in
`lib/backends/index.js`; nothing in the page changes.

## Network and permissions

`next start` binds to 127.0.0.1; `feed-proxy.mjs` (started by both launchers)
is the only listener on the LAN and serves nothing but `/api/calendar.ics` on
port 3118. `lib/spaces.js` holds the allow/deny rules that confine the
spawned CLI's file tools to the space folder; `--disallowedTools` is where
instruction/settings files are denied. Change those two lists together.

## Notes

- The spaces folder is personal data (memory, plans, dropped files) and
  lives outside the repository; it is not under version control anywhere.
- The MCP config (`canvas-mcp.local.json`) is generated at runtime with the
  resolved server path — machine-specific, gitignored.
