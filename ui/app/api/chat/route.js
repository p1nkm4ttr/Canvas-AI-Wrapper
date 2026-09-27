import { spawn } from "child_process";
import { ensureSpace, validSpaceId } from "../../../lib/spaces";
import { getBackend } from "../../../lib/backends";

export const dynamic = "force-dynamic";

// POST {message, spaceId, courseName?, sessionId?, backend?, model?}
//   -> SSE stream of normalized events (see lib/backends/index.js), one per
//      `data:` line, then `event: done`.
export async function POST(req) {
  const { message, spaceId, courseName, sessionId, backend: backendId, model } = await req.json();
  if (!message || typeof message !== "string") {
    return Response.json({ error: "message required" }, { status: 400 });
  }
  if (!validSpaceId(spaceId)) {
    return Response.json({ error: "bad spaceId" }, { status: 400 });
  }
  const backend = getBackend(backendId);
  if (!backend.available()) {
    return Response.json({ error: `${backend.label} is not installed on this machine` }, { status: 400 });
  }

  const { dir, systemFile } = ensureSpace(spaceId, courseName);
  backend.prepareSpace(dir, systemFile);
  const { cmd, args, env, cwd } = backend.build({ dir, systemFile, sessionId, model });
  const translate = backend.translator();

  const child = spawn(cmd, args, { cwd, env });
  // The prompt goes via stdin: no shell, no argv quoting problems.
  child.stdin.write(message);
  child.stdin.end();

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      let buffer = "";
      let closed = false;
      const emit = (ev) => {
        if (closed) return;
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n\n`));
      };
      const handleLine = (line) => {
        if (!line.trim()) return;
        for (const ev of translate(line)) emit(ev);
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
        for (const line of lines) handleLine(line);
      });
      child.stderr.on("data", (chunk) => {
        emit({ type: "stderr", text: chunk.toString("utf-8") });
      });
      child.on("close", (code) => {
        if (buffer.trim()) handleLine(buffer);
        if (code !== 0) emit({ type: "spawn_error", code });
        finish();
      });
      child.on("error", (err) => {
        emit({ type: "spawn_error", error: String(err) });
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
