// The phone's calendar feed, on the LAN, and nothing else.
//
// The UI itself binds to 127.0.0.1: every other route (chat, upload, memory
// edits, opening folders) would otherwise be reachable by anyone on the same
// Wi-Fi — and the chat route drives a model with file tools on your quota.
// This server listens on all interfaces and forwards exactly one GET path to
// the local UI. Subscribe the phone to http://<lan-ip>:3118/api/calendar.ics.
import http from "http";

const PORT = Number(process.env.FEED_PORT || 3118);
const UI = process.env.UI_ORIGIN || "http://127.0.0.1:3117";
const ALLOWED = new Set(["/api/calendar.ics"]);

http
  .createServer(async (req, res) => {
    const path = (req.url || "").split("?")[0];
    if (req.method !== "GET" || !ALLOWED.has(path)) {
      res.writeHead(404, { "Content-Type": "text/plain" });
      res.end("not found");
      return;
    }
    try {
      const upstream = await fetch(UI + path);
      const body = Buffer.from(await upstream.arrayBuffer());
      res.writeHead(upstream.status, {
        "Content-Type": upstream.headers.get("content-type") || "text/calendar; charset=utf-8",
        "Cache-Control": "no-cache",
      });
      res.end(body);
    } catch {
      res.writeHead(502, { "Content-Type": "text/plain" });
      res.end("ui not running");
    }
  })
  .listen(PORT, "0.0.0.0", () => {
    console.log(`calendar feed for the phone: http://<lan-ip>:${PORT}/api/calendar.ics`);
  });
