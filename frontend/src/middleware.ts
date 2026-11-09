import { NextResponse, type NextRequest } from "next/server";

// The backend requires X-API-Key on every /api/v1 route when API_AUTH_KEY is set. The browser
// never sees the key: it calls the dashboard's own /api/v1 (proxied by the rewrite in
// next.config.mjs) and this middleware adds the header on the server side.
// Anyone who can open the dashboard can therefore use the API through it, so put the dashboard
// itself behind access control before exposing it publicly.
export function middleware(req: NextRequest) {
  const key = process.env.API_AUTH_KEY;
  if (!key) return NextResponse.next();
  const headers = new Headers(req.headers);
  headers.set("x-api-key", key);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  // WebSocket upgrades are excluded: Next 14 cannot run middleware on an upgrade request (it
  // crashes the proxy). The WebSocket authenticates with a short-lived ticket instead, fetched
  // over REST through this middleware (see getWebSocketUrl in src/lib/api.ts).
  matcher: "/api/v1/((?!ws/).*)",
};
