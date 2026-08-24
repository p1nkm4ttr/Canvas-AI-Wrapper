import { readSpaceFile, writeSpaceFile, validSpaceId } from "../../../lib/spaces";

export const dynamic = "force-dynamic";

export async function GET(req) {
  const url = new URL(req.url);
  const space = url.searchParams.get("space");
  const file = url.searchParams.get("file");
  if (!validSpaceId(space) || !["memory", "plan"].includes(file)) {
    return Response.json({ error: "bad params" }, { status: 400 });
  }
  try {
    return Response.json({ content: readSpaceFile(space, file) });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}

export async function PUT(req) {
  const { space, file, content } = await req.json();
  if (!validSpaceId(space) || !["memory", "plan"].includes(file)) {
    return Response.json({ error: "bad params" }, { status: 400 });
  }
  try {
    writeSpaceFile(space, file, content);
    return Response.json({ ok: true });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
