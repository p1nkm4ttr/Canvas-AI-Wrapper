// Backend: OpenAI Codex CLI (`codex exec`), ChatGPT subscription auth.
//
// Differences from Claude Code that shape this adapter (all measured against
// codex-cli 0.155 on Windows, not assumed):
//  - The coach prompt is passed as `developer_instructions` (measured: it is
//    appended to the model's instructions), so no instruction file sits in
//    the space for the model to tamper with.
//  - There are no Read/Write tools: the model edits files through a shell
//    that runs inside Codex's sandbox. `workspace-write` blocks writes
//    outside the space but does NOT block reads, so a steered Codex could
//    read files elsewhere on the machine. Its shell has no network, and the
//    one MCP tool that could carry data out (fetch_web_image) is disabled
//    for this backend via the server's CANVAS_DISABLE_TOOLS.
//  - Replies arrive as completed messages, not token deltas.
//  - The session id is the thread id from the first event; resume is
//    `codex exec resume <id>` from the same working directory.
import fs from "fs";
import os from "os";
import path from "path";
import { execFileSync } from "child_process";
import { agentBin } from "../spaces";

export const id = "codex";
export const label = "Codex (ChatGPT)";
export const defaultModel = "gpt-5.5";

const WIN = process.platform === "win32";

export function codexExe() {
  try {
    const out = execFileSync(WIN ? "where" : "which", ["codex"], { encoding: "utf-8", stdio: ["ignore", "pipe", "ignore"] });
    const first = out.split(/\r?\n/).find((l) => l.trim());
    if (first) return first.trim();
  } catch {}
  // The Codex desktop app bundles the CLI under a versioned folder.
  if (WIN && process.env.LOCALAPPDATA) {
    const bin = path.join(process.env.LOCALAPPDATA, "OpenAI", "Codex", "bin");
    try {
      const candidates = fs.readdirSync(bin)
        .map((d) => path.join(bin, d, "codex.exe"))
        .filter((p) => fs.existsSync(p))
        .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
      if (candidates[0]) return candidates[0];
    } catch {}
  }
  return null;
}

export function available() {
  return !!codexExe();
}

// The models this account can use, from the CLI's own cache when present.
const FALLBACK = [
  { id: "gpt-5.5", label: "GPT-5.5 · everyday" },
  { id: "gpt-6-astra", label: "GPT-6-Astra · maximum" },
];
export function models() {
  try {
    const cache = JSON.parse(fs.readFileSync(path.join(os.homedir(), ".codex", "models_cache.json"), "utf-8"));
    const list = (cache.models || [])
      .filter((m) => m.slug && !/review|reserve/i.test(m.slug))
      .map((m) => ({ id: m.slug, label: m.display_name || m.slug }));
    if (list.length) return [...list, { id: "default", label: "CLI default" }];
  } catch {}
  return [...FALLBACK, { id: "default", label: "CLI default" }];
}

// Nothing to write into the space; remove any instruction file that a
// previous version left there (Codex would read it).
export function prepareSpace(dir) {
  try { fs.unlinkSync(path.join(dir, "AGENTS.md")); } catch {}
}

function instructions(systemFile) {
  const notesFile = path.join(process.cwd(), "coach.codex.md");
  const notes = fs.existsSync(notesFile) ? fs.readFileSync(notesFile, "utf-8") : "";
  return fs.readFileSync(systemFile, "utf-8") + "\n\n" + notes;
}

// TOML basic strings escape like JSON strings for our purposes (backslashes,
// quotes), so JSON.stringify produces a valid TOML value.
const toml = (s) => JSON.stringify(s);

export function build({ dir, systemFile, sessionId, model }) {
  const cmd = codexExe();
  const common = [
    "--json",
    "--skip-git-repo-check",
    "-c", 'sandbox_mode="workspace-write"',
    "-c", 'web_search="disabled"',            // no outbound channel for a steered model
    "-c", 'model_reasoning_effort="medium"',  // the user's CLI default may be "high"
    "-c", `mcp_servers.canvas.command=${toml(agentBin("canvas-server"))}`,
    "-c", 'mcp_servers.canvas.env.CANVAS_DISABLE_TOOLS="fetch_web_image"',
    // The whole coach prompt, as a TOML string (well under the argv limit).
    "-c", `developer_instructions=${toml(instructions(systemFile))}`,
  ];
  if (model && model !== "default") common.push("-m", model);
  // `-` = read the prompt from stdin, same as the Claude path.
  const args = sessionId
    ? ["exec", "resume", sessionId, ...common, "-"]
    : ["exec", ...common, "-C", dir, "-"];
  const env = { ...process.env };
  delete env.OPENAI_API_KEY; // subscription login only, never a key
  return { cmd, args, env, cwd: dir };
}

export function translator() {
  let threadId = null;
  return (line) => {
    let obj;
    try { obj = JSON.parse(line); } catch { return []; }
    switch (obj.type) {
      case "thread.started":
        threadId = obj.thread_id || threadId;
        return [{ type: "init", sessionId: threadId }];
      case "item.started": {
        const it = obj.item || {};
        if (it.type === "command_execution") return [{ type: "tool", name: "shell" }];
        if (it.type === "mcp_tool_call") return [{ type: "tool", name: it.tool || it.name || "mcp" }];
        if (it.type === "web_search") return [{ type: "tool", name: "web_search" }];
        return [];
      }
      case "item.completed": {
        const it = obj.item || {};
        if (it.type === "agent_message" && it.text) return [{ type: "text", text: it.text + "\n\n" }];
        return [];
      }
      case "turn.completed":
        return [{ type: "result", sessionId: threadId, isError: false }];
      case "turn.failed":
      case "error":
        return [{ type: "result", sessionId: threadId, isError: true,
                  text: obj.message || obj.error?.message || "Codex reported an error." }];
      default:
        return [];
    }
  };
}
