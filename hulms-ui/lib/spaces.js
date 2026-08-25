import fs from "fs";
import path from "path";

// hulms-ui/ lives next to hulms-agent/ inside the project root.
export const PROJECT_ROOT = path.resolve(process.cwd(), "..");
export const SPACES_DIR = path.join(PROJECT_ROOT, "spaces");
export const AGENT_BIN = path.join(PROJECT_ROOT, "hulms-agent", ".venv", "Scripts");
// Generated at runtime (machine-specific absolute path; never committed).
const MCP_CONFIG_PATH = path.join(process.cwd(), "hulms-mcp.local.json");

export function ensureMcpConfig() {
  const config = {
    mcpServers: {
      hulms: { command: path.join(AGENT_BIN, "hulms-server.exe"), args: [] },
    },
  };
  fs.writeFileSync(MCP_CONFIG_PATH, JSON.stringify(config, null, 2));
  return MCP_CONFIG_PATH;
}
export const COACH_FILE = path.join(process.cwd(), "coach.md");
export const CLAUDE_EXE = path.join(
  process.env.USERPROFILE || "",
  ".local",
  "bin",
  "claude.exe"
);

const SPACE_ID = /^[a-z0-9][a-z0-9-]{0,40}$/;

export function validSpaceId(id) {
  return typeof id === "string" && SPACE_ID.test(id);
}

export function spaceDir(spaceId) {
  if (!validSpaceId(spaceId)) throw new Error(`bad space id: ${spaceId}`);
  return path.join(SPACES_DIR, spaceId);
}

const MEMORY_SEED = `# Memory

Long-lived notes the coach keeps about this course. Editable by hand.
`;
const PLAN_SEED = `# Plan

The current plan for this course. Editable by hand.
`;

export function ensureSpace(spaceId, courseName) {
  const dir = spaceDir(spaceId);
  fs.mkdirSync(dir, { recursive: true });
  const memory = path.join(dir, "memory.md");
  const plan = path.join(dir, "plan.md");
  if (!fs.existsSync(memory)) fs.writeFileSync(memory, MEMORY_SEED);
  if (!fs.existsSync(plan)) fs.writeFileSync(plan, PLAN_SEED);

  // Current folder contents, injected into the system prompt so the coach
  // KNOWS what the student dropped in — it has no directory-listing tool
  // beyond Glob, and must never claim "no syllabus" while one sits here.
  const dropped = fs.readdirSync(dir).filter((f) => f !== "system.md");
  const listing = dropped
    .map((f) => {
      const size = fs.statSync(path.join(dir, f)).size;
      return `- ${f} (${size < 2048 ? size + " B" : Math.round(size / 1024) + " KB"})`;
    })
    .join("\n");

  // Per-space system prompt: the shared coach plus the course binding.
  const coach = fs.readFileSync(COACH_FILE, "utf-8");
  const header =
    (spaceId === "general"
      ? "## Space: General (no single course — cross-course questions live here)\n\n"
      : `## Space: ${courseName || spaceId}\n\nEvery question in this space is about this course unless the student says otherwise.\n\n`) +
    `### Files in this space folder right now\n\n${listing}\n\nRead PDF/PPTX/DOCX files with the read_local_document tool (pass the filename); Read is only for plain-text files like memory.md.\n\n`;
  const systemFile = path.join(dir, "system.md");
  fs.writeFileSync(systemFile, header + coach);
  return { dir, systemFile };
}

export function readSpaceFile(spaceId, file) {
  if (!["memory", "plan"].includes(file)) throw new Error("bad file");
  const p = path.join(spaceDir(spaceId), `${file}.md`);
  return fs.existsSync(p) ? fs.readFileSync(p, "utf-8") : "";
}

export function writeSpaceFile(spaceId, file, content) {
  if (!["memory", "plan"].includes(file)) throw new Error("bad file");
  fs.mkdirSync(spaceDir(spaceId), { recursive: true });
  fs.writeFileSync(path.join(spaceDir(spaceId), `${file}.md`), content ?? "");
}

export const ALLOWED_TOOLS = [
  "mcp__hulms__get_agenda",
  "mcp__hulms__get_courses",
  "mcp__hulms__get_assignment",
  "mcp__hulms__get_announcements",
  "mcp__hulms__get_calendar",
  "mcp__hulms__get_todo",
  "mcp__hulms__get_peer_reviews",
  "mcp__hulms__get_syllabus",
  "mcp__hulms__get_grade_weights",
  "mcp__hulms__get_my_grades",
  "mcp__hulms__get_document_images",
  "mcp__hulms__render_document_pages",
  "mcp__hulms__crop_image",
  "mcp__hulms__fetch_web_image",
  "mcp__hulms__get_course_map",
  "mcp__hulms__create_planner_note",
  "mcp__hulms__get_my_submission",
  "mcp__hulms__get_study_context",
  "mcp__hulms__get_file_text",
  "mcp__hulms__search_course_content",
  "mcp__hulms__get_announcement_context",
  "mcp__hulms__read_local_document",
  "mcp__hulms__index_course_files",
  "mcp__hulms__add_plan_event",
  "mcp__hulms__list_plan_events",
  "mcp__hulms__delete_plan_event",
  "mcp__hulms__log_retrieval_item",
  "mcp__hulms__get_due_reviews",
  "mcp__hulms__record_review_result",
  "WebSearch",
  "WebFetch",
  "Read",
  "Write",
  "Edit",
  "Glob",
].join(",");
