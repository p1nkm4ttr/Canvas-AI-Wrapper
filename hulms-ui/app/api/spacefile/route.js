import fs from "fs";
import path from "path";
import { PROJECT_ROOT } from "../../../lib/spaces";

export const dynamic = "force-dynamic";

const TYPES = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
  ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
  ".pdf": "application/pdf",
};

// GET /api/spacefile?p=<path relative to spaces/> — serve extracted figures
// and dropped files so the chat can render them inline. Containment enforced.
export async function GET(req) {
  const url = new URL(req.url);
  const rel = url.searchParams.get("p") || "";
  const root = path.resolve(PROJECT_ROOT, "spaces");
  const target = path.resolve(root, rel);
  if (!target.startsWith(root + path.sep) || !fs.existsSync(target) || !fs.statSync(target).isFile()) {
    return new Response("not found", { status: 404 });
  }
  const type = TYPES[path.extname(target).toLowerCase()];
  if (!type) return new Response("unsupported type", { status: 415 });
  return new Response(fs.readFileSync(target), {
    headers: { "Content-Type": type, "Cache-Control": "no-cache" },
  });
}
