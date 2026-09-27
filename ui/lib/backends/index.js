// The chat route talks to a "backend": a subscription-authenticated agent
// CLI that can call our Canvas MCP server. Each backend module exports:
//   id, label, defaultModel, available(), models(),
//   prepareSpace(dir, systemFile), build({dir, systemFile, sessionId, model})
//     -> {cmd, args, env, cwd}, and translator() -> (line) => events[]
// where events are {type:"init", sessionId} | {type:"text", text} |
// {type:"tool", name} | {type:"result", sessionId, isError, text?}.
// The page never sees a CLI's raw output.
import * as claude from "./claude";
import * as codex from "./codex";

export const BACKENDS = { claude, codex };
export const DEFAULT_BACKEND = "claude";

export function getBackend(id) {
  return BACKENDS[id] || BACKENDS[DEFAULT_BACKEND];
}

export function describeBackends() {
  return Object.values(BACKENDS).map((b) => ({
    id: b.id,
    label: b.label,
    available: b.available(),
    defaultModel: b.defaultModel,
    models: b.models(),
  }));
}
