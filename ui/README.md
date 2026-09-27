# ui

The local chat UI. One Next.js page on `127.0.0.1:3117`; an API route spawns an agent CLI (`claude -p` or `codex exec`, chosen in the model picker) per message and streams its output to the browser. No agent loop and no API key: the CLI's subscription sign-in does the work, and all Canvas access goes through the `canvas` MCP server in [`../agent`](../agent/README.md).

### Run it

1. Install dependencies. Node.js 22 or newer.

```bash
npm install
```

2. Start the production build. It builds once (or when you pass `--build`), serves the bundle, starts the phone feed proxy on port 3118, and opens the browser.

```bash
start.cmd          # Windows
```

```bash
./start.sh         # macOS
```

3. For UI development, use the dev server instead. Hot reload, but slower and with React's double rendering.

```bash
dev.cmd            # Windows
```

```bash
./dev.sh           # macOS
```

> [!NOTE]
> `next build` deletes `.next` while it works, so never build while a production server is running from this folder; stop it first.

### How it is put together

1. **Course spaces.** Every course, plus General, is a space: a directory under `~/CanvasCoach/spaces/<id>/` (or `CANVAS_SPACES_DIR`) that becomes the working directory of the spawned CLI. `lib/spaces.js` creates it, seeds `memory.md` and `plan.md`, and regenerates `system.md` before every message from `coach.md` plus a listing of the files you dropped in.

2. **Conversations.** Each space has named chats, stored in the browser. A chat remembers the backend and session id that created it, so it resumes there on the next message (`--resume` for Claude Code, `codex exec resume` for Codex). Switching backends on an existing chat starts a fresh session and says so.

3. **Streaming.** The route in `app/api/chat/route.js` spawns the CLI with the prompt on stdin, hands each stdout line to the backend's translator, and forwards the resulting events as SSE. The page renders four event types and nothing else: `init`, `text`, `tool`, `result`.

4. **Panels.** The memory and plan buttons open the space's files for editing in place, bound to the space they were loaded from. Drop files with the upload button or open the folder directly.

### Backends

`lib/backends/<name>.js` is the whole contract with a CLI. To add one:

1. Create a module exporting `id`, `label`, `defaultModel`, `available()`, `models()`, `prepareSpace(dir, systemFile)`, `build({dir, systemFile, sessionId, model})` returning `{cmd, args, env, cwd}`, and `translator()` returning a function from one output line to an array of events.

2. List it in `lib/backends/index.js`. `app/api/backends` reports it to the picker automatically, marked "not installed" until `available()` is true.

3. Confine it. Claude Code is confined by permission rules (`ALLOWED_TOOLS` / `DISALLOWED_TOOLS` in `lib/spaces.js`); Codex by its `workspace-write` sandbox plus `web_search="disabled"` and `CANVAS_DISABLE_TOOLS=fetch_web_image` (see `lib/backends/codex.js`). A new backend needs an answer of its own, measured, not assumed.

> [!WARNING]
> In Claude Code, `Edit(...)` rules govern every file-writing tool; a `Write(...)` rule is silently ignored. Express denies as `Edit`. This was measured with a real `claude -p` run, and the comment in `lib/spaces.js` says so for a reason.

### Three traps

> [!WARNING]
> Never pass `--bare` to Claude Code. It skips subscription auth and demands an API key. Claude Code is pinned at 2.1.186 here; re-test after upgrades in case `-p` defaults change.

> [!WARNING]
> `ANTHROPIC_API_KEY` is deleted from the child environment in `lib/backends/claude.js`, and `OPENAI_API_KEY` in `codex.js`. If a key exists, the CLI prefers it over the subscription and bills it silently.

> [!WARNING]
> `claude -p` starts in manual permission mode. Every tool it may need must be in `--allowedTools`, or the run blocks forever waiting for an approval nobody can give.

### Network and permissions

1. `next start` binds to `127.0.0.1`. Nobody on the Wi-Fi can reach the chat, uploads or memory files.

2. `feed-proxy.mjs`, started by both launchers, is the only listener on the LAN. It serves `/api/calendar.ics` on port 3118 and 404s everything else. Subscribe the phone to `http://<lan-ip>:3118/api/calendar.ics`.

3. Uploads refuse the names Claude Code and Codex read as instructions (`CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`) and anything starting with a dot, so a dropped file can never become the coach's orders.

4. `canvas-mcp.local.json` is generated at runtime with the resolved server path. It is machine-specific and gitignored.
