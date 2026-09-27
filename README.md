# Canvas Coach

A personal AI study assistant for Canvas LMS. One student, one machine, no cloud. A local MCP server turns your Canvas account into tools, and a model does the reasoning through Claude Desktop, or through the bundled chat UI driving Claude Code or the Codex CLI. There is no agent loop, no API key, and no summarizer: tools fetch, the model reasons, and every answer carries a link back to Canvas.

What you get once it is set up:

- Answers about deadlines, announcements, course structure and grades, grounded in live Canvas data. Missing data is reported as missing, never guessed.
- Study mode: point it at a quiz, module or date and it reads the actual material (PDF, PPTX with speaker notes, DOCX, notebooks) and quizzes you from it. Misses come back on a spaced schedule.
- Syllabus recovery and grade standing, even at institutions where `points_possible` is 0 and the real weights only live in a PDF.
- A calendar feed for your phone with Canvas deadlines, your study plan and a daily brief.
- A chat UI with a space per course: named conversations, a `memory.md` and `plan.md` the coach maintains, and a folder for dropped files.

The sub-READMEs go deeper: [`agent/README.md`](agent/README.md) for the MCP server and CLIs, [`ui/README.md`](ui/README.md) for the chat UI.

## Set up the Canvas MCP server

### Get a Canvas token

1. In Canvas, open **Account → Settings** and scroll to **Approved Integrations**.

2. Click **New Access Token**, give it a purpose, leave the expiry blank, and copy the token.

> [!WARNING]
> This token can read your grades and submit work on your behalf. Treat it like a password. It only ever goes into a gitignored `.env` file; nothing in this project prints, logs or commits it.

### Install the server

1. Clone the repository and create a virtual environment inside `agent/`.

```bash
git clone https://github.com/p1nkm4ttr/Canvas-AI-Wrapper.git
cd Canvas-AI-Wrapper/agent
python -m venv .venv
```

2. Install the package. Python 3.10 or newer is required.

```bash
.venv\Scripts\pip install -e .      # Windows
```

```bash
.venv/bin/pip install -e .          # macOS / Linux
```

3. Create `agent/.env` with your Canvas host, the token from above, and your IANA timezone. Every due date the tools return is converted into this zone.

```
CANVAS_HOST=https://your-school.instructure.com
CANVAS_TOKEN=paste-your-token-here
TIMEZONE=Europe/London
```

4. Prove the token works before anything else.

```bash
.venv\Scripts\canvas-server --test   # Windows
```

```bash
.venv/bin/canvas-server --test       # macOS / Linux
```

You should see your own name:

```
✓ API connection successful! Authenticated as: Your Name
```

> [!TIP]
> If that line printed, everything downstream will work. If it did not, nothing else is worth debugging: re-check the host (no `/api/v1`, the normalizer adds it) and re-paste the token.

5. Try the command-line tools. They use the same client and cache as the MCP server.

```bash
.venv\Scripts\canvas-agenda 14       # the next two weeks, in your timezone
```

```bash
.venv\Scripts\canvas-courses         # every course, active and completed
```

> [!NOTE]
> **Where the data lives.** Canvas responses and extracted document text are cached in `agent/canvas.db` (SQLite with FTS5). Re-runs are free; deadlines are always fetched fresh. Nothing is sent anywhere except to your Canvas host.

### Connect it to Claude Desktop

1. Open Claude Desktop's config file: `%APPDATA%\Claude\claude_desktop_config.json` on Windows, `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS.

2. Add the server, using the absolute path to the venv's binary.

```json
{
  "mcpServers": {
    "canvas": {
      "command": "C:\\path\\to\\Canvas-AI-Wrapper\\agent\\.venv\\Scripts\\canvas-server.exe",
      "args": []
    }
  }
}
```

On macOS the command is `/path/to/Canvas-AI-Wrapper/agent/.venv/bin/canvas-server`.

3. Restart Claude Desktop and ask it something only your Canvas knows:

```
what's due this week?
```

```
what did Module 3 of Data Structures cover?
```

Both answers should carry working `https://<your-host>/courses/...` links. The `course` argument of every tool accepts a name, a code or an id, so "Data Structures" works without a lookup.

## Set up the chat UI

The UI is optional. Claude Desktop already gives you the whole tool surface. The UI adds course spaces, persistent memory and plans, dropped files, the phone calendar feed, and a choice of model backend.

### Install an agent CLI

The UI does not call a model API. It spawns a subscription-authenticated CLI per message and streams its output. Install at least one and sign in.

1. **Claude Code**, using the [official installer](https://claude.com/claude-code), then:

```bash
claude auth login
```

2. **Codex CLI** (optional second backend). On Windows the Codex desktop app bundles the CLI and the UI finds it automatically; elsewhere install it and put `codex` on your PATH, then:

```bash
codex login
```

> [!NOTE]
> Never set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` expecting the UI to use them. It deliberately strips both from the CLI's environment so a key can never be billed instead of your subscription. Sign-in is the only supported auth.

### Run the UI

1. Install the dependencies. Node.js 22 or newer is required.

```bash
cd ui
npm install
```

2. Start it. The production launcher builds once, serves on port 3117, starts the phone feed proxy, and opens your browser.

```bash
start.cmd        # Windows
```

```bash
./start.sh       # macOS
```

Use `dev.cmd` / `./dev.sh` only when changing UI code; the production build is noticeably snappier.

3. Pick a course on the left and a model at the top. The picker groups models by backend and only lists CLIs that are installed. Sonnet is the default because coach work is tool orchestration and quizzing, which does not need a flagship burning your usage limits.

4. Ask something. The first message in a space spawns the CLI with your Canvas tools attached; you will see tool chips (`get_agenda`, `get_study_context`, ...) as it works.

> [!NOTE]
> **Course spaces.** Each course has a folder under `~/CanvasCoach/spaces/<id>/` holding `memory.md` (long-lived notes the coach keeps about the course), `plan.md` (the current plan), and anything you drop in through the **folder** or **upload** buttons, such as a syllabus PDF exported from an SSO-gated tool. The coach can read and search dropped files. The folder is outside the repository on purpose; override the location with `CANVAS_SPACES_DIR` if you want it elsewhere.

5. Each chat remembers the backend and session that created it, so a course conversation continues across days. Switching backends on an existing chat starts a fresh session there; the UI tells you when that happens.

### Put the calendar on your phone

The UI serves an iCalendar feed merging Canvas deadlines, the coach's planned study sessions, and the daily brief. Only this one path is exposed on your Wi-Fi, on a separate port, by `ui/feed-proxy.mjs`; the UI itself stays on `127.0.0.1`.

1. Allow the feed port through the firewall. On Windows, in an **administrator** PowerShell:

```powershell
netsh advfirewall firewall add rule name="Canvas Coach calendar feed (port 3118)" dir=in action=allow protocol=TCP localport=3118 profile=private
```

On macOS the firewall, if it is on, prompts once for `node`; allow it.

2. Find this machine's LAN address.

```powershell
ipconfig                    # Windows: the IPv4 Address of your Wi-Fi adapter
```

```bash
ipconfig getifaddr en0      # macOS
```

3. On the phone, on the same Wi-Fi: **Settings → Calendar → Accounts → Add Account → Other → Add Subscribed Calendar**, and enter:

```
http://<lan-ip>:3118/api/calendar.ics
```

Choose **Continue** when iOS warns that the feed is not SSL.

> [!NOTE]
> The feed updates only while the UI is running and the phone is on the same network. Phones keep showing the last fetched copy when it is not.

### Schedule the daily brief and weekly backup

The brief re-indexes your active courses, then writes one all-day calendar event with overnight announcements, the week's deadlines, newly posted files and reviews due. The backup zips the database and your spaces (never the token) and keeps the last ten.

1. **Windows.** In PowerShell, with `$root` set to your clone:

```powershell
$root = "C:\path\to\Canvas-AI-Wrapper"; $bin = "$root\agent\.venv\Scripts"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "Canvas Daily Brief" -Action (New-ScheduledTaskAction -Execute "$bin\canvas-brief.exe" -Argument "--force" -WorkingDirectory "$root\agent") -Trigger (New-ScheduledTaskTrigger -Daily -At 07:30) -Settings $settings
Register-ScheduledTask -TaskName "Canvas Weekly Backup" -Action (New-ScheduledTaskAction -Execute "$bin\canvas-backup.exe" -WorkingDirectory "$root\agent") -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 20:00) -Settings $settings
```

For a brief on days the laptop was asleep at 07:30, drop a one-line `canvas-brief.vbs` into the Startup folder (`shell:startup`). It runs at logon and skips itself when today's brief already exists:

```vbscript
CreateObject("Wscript.Shell").Run """C:\path\to\Canvas-AI-Wrapper\agent\.venv\Scripts\canvas-brief.exe""", 0, False
```

2. **macOS.** launchd replays a run missed during sleep, so there is no logon job. Follow [`ui/launchd/README.md`](ui/launchd/README.md); it fills the two plists' placeholders with `sed` and loads them.

> [!TIP]
> A brief that could not fetch something says so in the event itself and exits non-zero, so the scheduler's "last run result" shows the gap. It never reports "all quiet" on a failed fetch.

## Study with it

1. Index a course once so cross-week search works. Either ask the coach to do it or run:

```bash
.venv\Scripts\canvas-extract "Data Structures"
```

2. Ask for a study session on something concrete.

```
help me study for the Week 3 quiz in Data Structures
```

The coach walks from the quiz to its module, reads the material, and asks you questions drawn from it. Every miss is logged.

3. Start later sessions with a review. Missed items resurface on a 1/3/7/14/30 day schedule.

```
what should I review today?
```

4. Plan a term. Drop the syllabus PDF into the course's folder if Canvas's syllabus field is empty, then:

```
plan the semester for this course and put the assessments on my calendar
```

The grade breakdown is recovered structurally when the course uses weighted groups, and parsed conservatively from the syllabus otherwise. It is accepted only when the components sum to about 100%.

> [!NOTE]
> Canvas does not expose quiz questions to students, and this project does not try. "Study for the quiz" means studying the material the quiz's module contains.

## How it is built

```
agent/     Python MCP server (fork of vishalsachdev/canvas-mcp, MIT)
           29 tools over stdio · SQLite cache + FTS5 · CLIs:
           canvas-server, canvas-audit, canvas-agenda, canvas-extract,
           canvas-brief, canvas-ics, canvas-backup, canvas-courses
ui/        Next.js chat UI on 127.0.0.1:3117 · spawns the chosen agent
           CLI (claude -p or codex exec) per message, its output translated
           to SSE events by a backend adapter · calendar feed at
           /api/calendar.ics, re-served on LAN port 3118 by feed-proxy.mjs
~/CanvasCoach/spaces/
           per-course working dirs: memory, plans, dropped files, extracted
           figures (personal data, outside the repo; CANVAS_SPACES_DIR)
```

### Backends

| | Claude Code | Codex CLI |
|---|---|---|
| Sign-in | Claude subscription | ChatGPT |
| Instructions | `--append-system-prompt-file` | `developer_instructions` config override |
| Canvas tools | MCP config file | `mcp_servers.canvas.*` config overrides |
| Streaming | token by token | per message |
| Confinement | permission rules (allow list + deny list) | OS sandbox: writes only inside the space; reads are not restricted, so `fetch_web_image` and web search are switched off for this backend |
| Resume | `--resume <session>` | `codex exec resume <thread>` |

Adapters live in `ui/lib/backends/`; each turns its CLI's output into the same four events the page renders. A backend can ask the server to withhold tools by setting `CANVAS_DISABLE_TOOLS` in the server's environment.

### What is exposed, and what the model can touch

- The UI binds to `127.0.0.1`. The only listener on the LAN is `feed-proxy.mjs`, which forwards one GET path and 404s everything else.
- Spaces live outside the repository, so the coach's working directory has no `CLAUDE.md` above it and no project tree beside it.
- Claude Code runs with its file tools confined by permission rules to the space folder plus the shared figure store, with explicit denies on the files it reads as instructions or settings (`CLAUDE.md`, `.claude/`, `.mcp.json`). Uploads refuse those names too. It is never allowed a shell. Codex works only through its sandboxed shell, which cannot write outside the space and has no network.
- Canvas-authored text (bodies, titles, file names, notes) is fenced as untrusted before the model sees it, and a CI test insists every read tool declares how.
- Every outbound download (course files, verifier links harvested from assignment text, web images) goes through one guarded downloader: public hosts only apart from the Canvas host itself, every redirect hop re-checked, bodies streamed and abandoned at the size cap. A harvested link counts as a file reference only if it is on the Canvas origin.
- The chat renders images only from the app's own file route. An image the model points at any other host is shown as a link and never fetched, so the browser cannot be turned into a data channel.
- Date arithmetic and grade math happen in the tools, never in the model.

## Honest limitations

- The tools know only what is in Canvas. Deadlines moved verbally or on WhatsApp are invisible.
- Scanned (image-only) PDFs are skipped rather than half-OCRed.
- Simple Syllabus and other SSO-gated LTI tools are unreachable. Export the PDF and drop it into the course's space folder instead.
- Quality is capped by instructor data hygiene. A course that is one announcement saying "see syllabus" stays that way.

> [!NOTE]
> **macOS and iCloud.** If the repo lives in an iCloud-synced folder (Desktop or Documents), iCloud marks dot-folders hidden and Python then ignores the venv's `.pth` file, so `import canvas_mcp` fails. Keep the repo outside iCloud, or create the venv as `venv.nosync` and symlink `.venv` to it. Keep `canvas.db` out of any synced folder too; two syncing writers corrupt SQLite.

## Credits and license

The MCP server is a fork of [vishalsachdev/canvas-mcp](https://github.com/vishalsachdev/canvas-mcp) (MIT, license retained in `agent/LICENSE`), stripped from its ~90-tool multi-persona hosted design to a single-user student surface, with study mode, extraction, syllabus recovery, spaced retrieval, grades, the calendar pipeline and the backend layer added on top.
