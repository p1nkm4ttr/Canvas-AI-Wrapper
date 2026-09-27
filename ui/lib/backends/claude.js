// Backend: Claude Code (`claude -p`), subscription auth.
//
// The reference backend. Its permission rules are what confine the coach's
// file tools (see ALLOWED_TOOLS / DISALLOWED_TOOLS in lib/spaces.js), and
// its stream-json output is translated here into the small event vocabulary
// the page renders: init, text, tool, result.
import fs from "fs";
import path from "path";
import {
  ALLOWED_TOOLS,
  DISALLOWED_TOOLS,
  CLAUDE_EXE,
  FIGURES_DIR,
  ensureMcpConfig,
} from "../spaces";

export const id = "claude";
export const label = "Claude Code";
export const defaultModel = "sonnet";

export function available() {
  return fs.existsSync(CLAUDE_EXE);
}

export function models() {
  return [
    { id: "haiku", label: "haiku · fastest" },
    { id: "sonnet", label: "sonnet · everyday" },
    { id: "opus", label: "opus · hard problems" },
    { id: "fable", label: "fable · maximum" },
    { id: "default", label: "CLI default" },
  ];
}
const MODEL_IDS = new Set(["haiku", "sonnet", "opus", "fable"]);

// Nothing to add to the space; but make sure no other backend's
// instruction file lingers where Claude Code might pick it up.
export function prepareSpace(dir) {
  try { fs.unlinkSync(path.join(dir, "AGENTS.md")); } catch {}
}

export function build({ dir, systemFile, sessionId, model }) {
  fs.mkdirSync(FIGURES_DIR, { recursive: true }); // --add-dir needs it to exist
  const args = [
    "-p",
    "--mcp-config", ensureMcpConfig(),
    "--allowedTools", ALLOWED_TOOLS,
    "--disallowedTools", DISALLOWED_TOOLS,
    "--add-dir", FIGURES_DIR,
    "--append-system-prompt-file", systemFile,
    "--output-format", "stream-json",
    "--verbose",
    "--include-partial-messages",
  ];
  if (sessionId) args.push("--resume", sessionId);
  if (MODEL_IDS.has(model)) args.push("--model", model);

  // Build brief trap #2: if ANTHROPIC_API_KEY exists anywhere, Claude Code
  // uses it instead of the subscription. Strip it (and never pass --bare).
  const env = { ...process.env };
  delete env.ANTHROPIC_API_KEY;
  return { cmd: CLAUDE_EXE, args, env, cwd: dir };
}

// One stdout line of `--output-format stream-json --verbose` -> zero or
// more normalized events. --verbose also emits full assistant/user messages
// carrying complete tool results (a study context is ~77 KB); those are
// dropped here so the browser never parses them.
export function translator() {
  return (line) => {
    let obj;
    try { obj = JSON.parse(line); } catch { return []; }
    if (obj.type === "system" && obj.subtype === "init") {
      return [{ type: "init", sessionId: obj.session_id }];
    }
    if (obj.type === "result") {
      return [{ type: "result", sessionId: obj.session_id, isError: !!obj.is_error,
                text: obj.is_error ? obj.result : undefined }];
    }
    if (obj.type === "stream_event") {
      const ev = obj.event || {};
      if (ev.type === "content_block_delta" && ev.delta?.type === "text_delta") {
        return [{ type: "text", text: ev.delta.text }];
      }
      if (ev.type === "content_block_start" && ev.content_block?.type === "tool_use") {
        return [{ type: "tool", name: String(ev.content_block.name || "").replace("mcp__canvas__", "") }];
      }
    }
    return [];
  };
}
