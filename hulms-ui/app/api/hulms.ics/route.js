import { execFile } from "child_process";
import path from "path";
import { AGENT_BIN } from "../../../lib/spaces";

export const dynamic = "force-dynamic";

// GET /api/hulms.ics — the merged calendar (Canvas deadlines + plan events).
// Subscribe from the iPhone: Settings > Calendar > Accounts > Add Account >
// Other > Add Subscribed Calendar > http://<this-pc's-LAN-ip>:3117/api/hulms.ics
export async function GET() {
  const exe = path.join(AGENT_BIN, "hulms-ics.exe");
  const ics = await new Promise((resolve) => {
    execFile(exe, [], { timeout: 120_000, maxBuffer: 8 * 1024 * 1024 }, (err, stdout) => {
      resolve(err && !stdout ? null : stdout);
    });
  });
  if (!ics || !ics.includes("BEGIN:VCALENDAR")) {
    return new Response("calendar generation failed", { status: 500 });
  }
  return new Response(ics, {
    headers: {
      "Content-Type": "text/calendar; charset=utf-8",
      "Content-Disposition": 'inline; filename="hulms.ics"',
      "Cache-Control": "no-cache",
    },
  });
}
