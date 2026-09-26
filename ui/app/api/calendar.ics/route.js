import { execFile } from "child_process";
import { agentBin } from "../../../lib/spaces";

export const dynamic = "force-dynamic";

// GET /api/calendar.ics — the merged calendar (Canvas deadlines + plan events).
// Subscribe from the iPhone: Settings > Calendar > Accounts > Add Account >
// Other > Add Subscribed Calendar > http://<this-pc's-LAN-ip>:3117/api/calendar.ics
export async function GET() {
  const exe = agentBin("canvas-ics");
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
      "Content-Disposition": 'inline; filename="calendar.ics"',
      "Cache-Control": "no-cache",
    },
  });
}
