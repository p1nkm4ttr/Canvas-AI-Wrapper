import { execFile } from "child_process";
import path from "path";
import { AGENT_BIN } from "../../../lib/spaces";

export const dynamic = "force-dynamic";

export async function GET() {
  const exe = path.join(AGENT_BIN, "hulms-courses.exe");
  const body = await new Promise((resolve) => {
    execFile(exe, [], { timeout: 90_000 }, (err, stdout) => {
      if (err && !stdout) {
        resolve({ error: String(err) });
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch {
        resolve({ error: "could not parse course list", raw: String(stdout).slice(0, 500) });
      }
    });
  });
  return Response.json(body);
}
