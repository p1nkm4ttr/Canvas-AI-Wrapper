import { describeBackends, DEFAULT_BACKEND } from "../../../lib/backends";

export const dynamic = "force-dynamic";

// GET -> which agent CLIs are installed here and the models each offers.
export async function GET() {
  return Response.json({ default: DEFAULT_BACKEND, backends: describeBackends() });
}
