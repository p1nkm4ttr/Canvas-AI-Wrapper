import { spawn } from "child_process";
import {
  ALLOWED_TOOLS,
  CLAUDE_EXE,
  ensureMcpConfig,
  ensureSpace,
  validSpaceId,
} from "../../../lib/spaces";

export const dynamic = "force-dynamic";

// Selectable models; anything else falls through to the CLI's configured
// default. Sonnet is the UI default — coach work is tool orchestration and
// quizzing, which doesn't need the flagship burning the usage limits.
const ALLOWED_MODELS = new Set(["sonnet", "haiku", "opus", "fable"]);

// POST {message, spaceId, courseName?, sessionId?, model?} -> SSE stream of
// the claude -p stream-json lines, one per `data:` event.
export async function POST(req) {
  const { message, spaceId, courseName, sessionId, model } = await req.json();
  if (!message || typeof message !== "string") {
    return Response.json({ error: "message required" }, { status: 400 });
  }
  if (!validSpaceId(spaceId)) {
    return Response.json({ error: "bad spaceId" }, { status: 400 });
  }

  const { dir, systemFile } = ensureSpace(spaceId, courseName);

  const args = [
    "-p",
    "--mcp-config", ensureMcpConfig(),
    "--allowedTools", ALLOWED_TOOLS,
    "--append-system-prompt-file", systemFile,
    "--output-format", "stream-json",
    "--verbose",
    "--include-partial-messages",
  ];
  if (sessionId) args.push("--resume", sessionId);
  if (ALLOWED_MODELS.has(model)) args.push("--model", model);

  // Build brief trap #2: if ANTHROPIC_API_KEY exists anywhere, Claude Code
  // uses it instead of the subscription. Strip it (and never pass --bare).
  const env = { ...process.env };
  delete env.ANTHROPIC_API_KEY;

  const child = spawn(CLAUDE_EXE, args, { cwd: dir, env });
  // The prompt goes via stdin: no shell, no argv quoting problems.
  child.stdin.write(message);
  child.stdin.end();

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      let buffer = "";
      let closed = false;
      // Forward only what the page renders. --verbose stream-json also
      // emits full assistant/user messages carrying complete tool results
      // (a study context is ~77 KB) and tool-argument deltas; shipping
      // those to the browser to be JSON.parsed and discarded was a real
      // source of UI jank.
      const wanted = (line) => {
        let obj;
        try { obj = JSON.parse(line); } catch { return false; }
        if (obj.type === "system") return obj.subtype === "init";
        if (obj.type === "result") return true;
        if (obj.type === "stream_event") {
          const ev = obj.event || {};
          if (ev.type === "content_block_delta") return ev.delta?.type === "text_delta";
          if (ev.type === "content_block_start") return ev.content_block?.type === "tool_use";
          return false;
        }
        return obj.type === "stderr" || obj.type === "spawn_error";
      };
      const send = (line) => {
        if (closed || !line.trim() || !wanted(line)) return;
        controller.enqueue(encoder.encode(`data: ${line}\n\n`));
      };
      const finish = () => {
        if (closed) return;
        closed = true;
        try {
          controller.enqueue(encoder.encode("event: done\ndata: {}\n\n"));
          controller.close();
        } catch {}
      };

      child.stdout.on("data", (chunk) => {
        buffer += chunk.toString("utf-8");
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) send(line);
      });
      child.stderr.on("data", (chunk) => {
        send(JSON.stringify({ type: "stderr", text: chunk.toString("utf-8") }));
      });
      child.on("close", (code) => {
        if (buffer.trim()) send(buffer);
        if (code !== 0) {
          send(JSON.stringify({ type: "spawn_error", code }));
        }
        finish();
      });
      child.on("error", (err) => {
        send(JSON.stringify({ type: "spawn_error", error: String(err) }));
        finish();
      });

      req.signal?.addEventListener("abort", () => {
        try { child.kill(); } catch {}
        finish();
      });
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
