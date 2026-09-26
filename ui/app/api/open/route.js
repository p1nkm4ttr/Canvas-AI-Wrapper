import { ensureSpace, openFolder, validSpaceId } from "../../../lib/spaces";

export const dynamic = "force-dynamic";

// POST {space, courseName?} -> open the space folder in Explorer / Finder (local tool).
export async function POST(req) {
  const { space, courseName } = await req.json();
  if (!validSpaceId(space)) {
    return Response.json({ error: "bad space" }, { status: 400 });
  }
  const { dir } = ensureSpace(space, courseName);
  openFolder(dir);
  return Response.json({ ok: true, dir });
}
