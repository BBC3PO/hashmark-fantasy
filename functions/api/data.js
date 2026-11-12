// GET /api/data — the newest data.json straight from GitHub, so the hourly refresh
// doesn't need a new Cloudflare deploy. Falls back to the deployed copy.
const REPO = "BBC3PO/hashmark-fantasy";
export async function onRequestGet({ request }) {
  try {
    const r = await fetch(`https://raw.githubusercontent.com/${REPO}/main/site/data.json`, { cf: { cacheTtl: 300, cacheEverything: true } });
    if (r.ok) return new Response(r.body, { headers: { "content-type": "application/json; charset=utf-8", "cache-control": "public, max-age=300" } });
  } catch (e) {}
  return fetch(new URL("/data.json", request.url));
}
