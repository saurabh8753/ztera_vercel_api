/**
 * ZTERA TeraBox API — Cloudflare Worker entrypoint.
 * Routes:
 *   GET /api/health
 *   GET /api/info?url=
 *   GET /api/playlist?url=&quality=&format=json|m3u8
 *   GET /api/download?url=&quality=
 */

import { resolveTeraboxLink, resolvePlaylist, resolveDownload, TeraBoxError } from "./terabox_core.js";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...CORS_HEADERS },
  });
}

function text(body, status = 200, contentType = "text/plain") {
  return new Response(body, {
    status,
    headers: { "Content-Type": contentType, ...CORS_HEADERS },
  });
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    const path = url.pathname;

    try {
      if (path === "/api/health") {
        return json({ service: "ZTERA TeraBox API (Cloudflare Worker)", status: "ok" });
      }

      if (path === "/api/info") {
        const target = url.searchParams.get("url");
        if (!target) return json({ success: false, error: "Missing 'url' query parameter" }, 400);
        const data = await resolveTeraboxLink(target);
        return json({ success: true, ...data });
      }

      if (path === "/api/playlist") {
        const target = url.searchParams.get("url");
        const quality = url.searchParams.get("quality") || "M3U8_AUTO_720";
        const format = (url.searchParams.get("format") || "json").toLowerCase();
        if (!target) return json({ success: false, error: "Missing 'url' query parameter" }, 400);

        const data = await resolvePlaylist(env, target, quality);
        if (format === "m3u8") {
          return text(data.manifest, 200, "application/vnd.apple.mpegurl");
        }
        return json({ success: true, ...data });
      }

      if (path === "/api/download") {
        const target = url.searchParams.get("url");
        const quality = url.searchParams.get("quality") || "M3U8_AUTO_720";
        if (!target) return json({ success: false, error: "Missing 'url' query parameter" }, 400);

        const data = await resolveDownload(env, target, quality);
        return json({ success: true, ...data });
      }

      return json({ success: false, error: "Not found. Try /api/health, /api/info, /api/playlist, /api/download" }, 404);
    } catch (e) {
      if (e instanceof TeraBoxError) {
        return json({ success: false, error: e.message }, 502);
      }
      console.error(e);
      return json({ success: false, error: `Internal error: ${e.message || String(e)}` }, 500);
    }
  },
};
