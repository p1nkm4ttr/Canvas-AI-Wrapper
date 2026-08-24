import { spawn } from "child_process";
import { ensureSpace, validSpaceId } from "../../../lib/spaces";

export const dynamic = "force-dynamic";

// POST {space, courseName?} -> open the space folder in Explorer (local tool).
export async function POST(req) {
  const { space, courseName } = await req.json();
  if (!validSpaceId(space)) {
    return Response.json({ error: "bad space" }, { status: 400 });
  }
  const { dir } = ensureSpace(space, courseName);
  spawn("explorer.exe", [dir], { detached: true, stdio: "ignore" }).unref();
  return Response.json({ ok: true, dir });
}
