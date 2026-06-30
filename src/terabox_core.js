/**
 * ZTERA TeraBox extraction core — Cloudflare Workers (JS) port of terabox_core.py
 * Pure fetch()-based, no Python/Chromium/ffmpeg dependency.
 */

const BASE_DOMAIN = "dm.1024tera.com";
const BASE_URL = `https://${BASE_DOMAIN}`;

const USER_AGENTS = [
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
];

const BYTES_PER_MB = 1048576;
const QUALITIES = ["M3U8_AUTO_1080", "M3U8_AUTO_720", "M3U8_AUTO_480", "M3U8_AUTO_360"];

export class TeraBoxError extends Error {}

function randChoice(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function logid() {
  // 18-digit pseudo-random id, same shape as the Python version
  const min = 400000000000000000n;
  const max = 999999999999999999n;
  const range = max - min;
  const rand = BigInt(Math.floor(Math.random() * Number(range)));
  return (min + rand).toString();
}

// ── Cookies (from env secrets COOKIES1, COOKIES2, ...) ─────────────────────

function loadCookiesList(env) {
  const list = [];
  for (let i = 1; i <= 9; i++) {
    const c = env[`COOKIES${i}`];
    if (c) list.push(c);
  }
  return list;
}

/** Minimal cookie-jar substitute: just stores a parsed "name=value; ..." string */
function makeSession(env) {
  const cookiesList = loadCookiesList(env);
  if (!cookiesList.length) {
    throw new TeraBoxError("No cookies configured (COOKIES1, COOKIES2... missing as Worker secrets)");
  }
  const raw = randChoice(cookiesList);
  // normalize to "k=v; k2=v2"
  const pairs = raw
    .split(";")
    .map((p) => p.trim())
    .filter((p) => p.includes("="));
  return { cookieHeader: pairs.join("; ") };
}

function headers(session, surl = "") {
  const h = {
    "User-Agent": randChoice(USER_AGENTS),
    Referer: surl
      ? `${BASE_URL}/wap/share/filelist?surl=${surl}`
      : `${BASE_URL}/wap/share/filelist`,
  };
  if (session.cookieHeader) h["Cookie"] = session.cookieHeader;
  return h;
}

function safeFilename(name) {
  return name.replace(/[\\/*?:"<>|]/g, "_");
}

// ── Surl extraction ──────────────────────────────────────────────────────

export function extractSurl(url) {
  url = url.trim();
  let token = null;
  const m = url.match(/\/s\/([A-Za-z0-9_-]+)/);
  if (m) {
    token = m[1];
  } else {
    try {
      const u = new URL(url);
      token = u.searchParams.get("surl") || url;
    } catch {
      token = url;
    }
  }
  if (token.startsWith("1") && token.length > 1) {
    return token.slice(1);
  }
  return token;
}

// ── jsToken + share info ────────────────────────────────────────────────

async function getJsToken(session, surl) {
  const url = `${BASE_URL}/wap/share/filelist?surl=${surl}&clearCache=1`;
  let lastErr = "Unknown error";

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const resp = await fetch(url, { headers: headers(session, surl) });
      const html = await resp.text();

      let m = html.match(/fn%28%22([A-Fa-f0-9]+)%22%29/);
      if (m) return m[1];

      m = html.match(/eval\(decodeURIComponent\(`([^`]+)`\)\)/);
      if (m) {
        const decoded = decodeURIComponent(m[1]);
        const m2 = decoded.match(/fn\("([A-Fa-f0-9]+)"\)/);
        if (m2) return m2[1];
      }

      lastErr = "Token patterns not found in HTML";
    } catch (e) {
      lastErr = String(e);
    }
    if (attempt < 2) await sleep(1000);
  }

  throw new TeraBoxError(`Could not extract jsToken after 3 attempts: ${lastErr}`);
}

async function getShareInfo(session, jsToken, surl) {
  const params = new URLSearchParams({
    app_id: "250528",
    shorturl: `1${surl}`,
    root: "1",
    web: "1",
    channel: "dubox",
    clienttype: "0",
    jsToken,
    t: String(Math.floor(Date.now() / 1000)),
    "dp-logid": logid(),
  });
  const h = headers(session, surl);
  h["Accept"] = "application/json, text/plain, */*";
  h["Origin"] = BASE_URL;

  const resp = await fetch(`${BASE_URL}/api/shorturlinfo?${params}`, { headers: h });
  const data = await resp.json();
  if (data.errno !== 0) {
    throw new TeraBoxError(`shorturlinfo failed: errno=${data.errno}`);
  }
  return data;
}

function buildStreamingUrl(shareid, uk, sign, timestamp, fsId, quality) {
  const params = new URLSearchParams({
    uk: String(uk),
    shareid: String(shareid),
    type: quality,
    fid: String(fsId),
    sign,
    timestamp: String(timestamp),
    jsToken: "",
    esl: "1",
    isplayer: "1",
    ehps: "1",
    clienttype: "0",
    app_id: "250528",
    web: "1",
    channel: "dubox",
    "dp-logid": logid(),
  });
  return `${BASE_URL}/share/streaming?${params}`;
}

// ── Resolve metadata + raw (single-chunk) streaming URLs ───────────────────

export async function resolveTeraboxLink(rawUrl) {
  const surl = extractSurl(rawUrl);
  if (!surl) throw new TeraBoxError("Could not parse a valid surl from the given URL");

  const tempSession = { cookieHeader: "" };
  const jsToken = await getJsToken(tempSession, surl);
  const info = await getShareInfo(tempSession, jsToken, surl);

  const files = info.list || [];
  if (!files.length) throw new TeraBoxError("No files found in this share");

  const f = files[0];
  const shareid = info.shareid;
  const uk = info.uk;
  const sign = info.sign;
  const timestamp = info.timestamp;
  const fsId = f.fs_id;

  const streamingUrls = {};
  for (const q of QUALITIES) {
    streamingUrls[q] = buildStreamingUrl(shareid, uk, sign, timestamp, fsId, q);
  }

  return {
    filename: f.server_filename || "video",
    size: parseInt(f.size || 0, 10),
    size_mb: Math.round((parseInt(f.size || 0, 10) / BYTES_PER_MB) * 100) / 100,
    thumb: (f.thumbs && (f.thumbs.url3 || f.thumbs.url1)) || null,
    fs_id: fsId,
    shareid,
    uk,
    sign,
    timestamp,
    surl,
    streaming_urls: streamingUrls,
  };
}

// ── Real chunk discovery (poll repeatedly — TeraBox returns ONE random
//    chunk per request) ─────────────────────────────────────────────────

async function discoverAllHlsChunks(
  session,
  shareid,
  uk,
  sign,
  timestamp,
  fsId,
  quality,
  surl = "",
  maxBudget = 40,
  timeBudgetMs = 18000
) {
  const startT = Date.now();
  const known = new Map(); // idx -> { idx, url, tsSize }
  let reqCount = 0;
  let noNewStreak = 0;
  let maxIdx = -1;

  while (true) {
    if (reqCount >= maxBudget || Date.now() - startT > timeBudgetMs) break;

    reqCount++;
    const url = buildStreamingUrl(shareid, uk, sign, timestamp, fsId, quality);
    let text;
    try {
      const resp = await fetch(url, { headers: headers(session, surl) });
      text = (await resp.text()).trim();
    } catch {
      await sleep(500);
      continue;
    }

    if (!text.startsWith("#EXTM3U")) {
      await sleep(300);
      continue;
    }

    for (const line of text.split("\n")) {
      const seg = line.trim();
      if (!seg || seg.startsWith("#")) continue;

      let parsed;
      try {
        parsed = new URL(seg);
      } catch {
        continue;
      }
      const tsSize = parseInt(parsed.searchParams.get("ts_size") || "0", 10);
      if (tsSize <= 0) continue;

      const m = parsed.pathname.match(/_(\d+)_ts\//);
      if (!m) continue;
      const idx = parseInt(m[1], 10);
      if (known.has(idx)) continue;

      parsed.searchParams.set("range", `0-${tsSize - 1}`);
      parsed.searchParams.set("len", String(tsSize));
      known.set(idx, { idx, url: parsed.toString(), tsSize });
    }

    const curMax = known.size ? Math.max(...known.keys()) : -1;
    if (curMax > maxIdx) {
      maxIdx = curMax;
      noNewStreak = 0;
    } else {
      noNewStreak++;
    }

    if (known.size) {
      const minIdx = Math.min(...known.keys());
      const maxK = Math.max(...known.keys());
      const complete = minIdx <= 1 && known.size === maxK - minIdx + 1;
      if (complete && noNewStreak >= 6) break;
    }

    await sleep(200);
  }

  if (!known.size) return [];
  return [...known.keys()].sort((a, b) => a - b).map((k) => known.get(k));
}

function buildSyntheticM3U8(chunks, segDuration = 4.0) {
  const lines = [
    "#EXTM3U",
    "#EXT-X-VERSION:3",
    `#EXT-X-TARGETDURATION:${Math.floor(segDuration) + 1}`,
    "#EXT-X-MEDIA-SEQUENCE:0",
    "#EXT-X-PLAYLIST-TYPE:VOD",
  ];
  for (const c of chunks) {
    lines.push(`#EXTINF:${segDuration.toFixed(3)},`);
    lines.push(c.url);
  }
  lines.push("#EXT-X-ENDLIST");
  return lines.join("\n");
}

export async function resolvePlaylist(env, rawUrl, preferredQuality = "M3U8_AUTO_720") {
  const base = await resolveTeraboxLink(rawUrl);
  const session = makeSession(env);

  const qualityOrder = [preferredQuality, ...QUALITIES.filter((q) => q !== preferredQuality)];

  for (const q of qualityOrder) {
    const chunks = await discoverAllHlsChunks(
      session,
      base.shareid,
      base.uk,
      base.sign,
      base.timestamp,
      base.fs_id,
      q,
      base.surl
    );
    if (chunks.length) {
      return {
        filename: base.filename,
        size: base.size,
        size_mb: base.size_mb,
        thumb: base.thumb,
        quality_used: q,
        chunks_found: chunks.length,
        manifest: buildSyntheticM3U8(chunks),
      };
    }
  }

  throw new TeraBoxError(
    "No playable chunks found at any quality — cookie likely expired/banned, or this file isn't transcoded for streaming on this account."
  );
}

// ── Direct CDN dlink (best-effort, auto-fallback to playlist) ──────────────

async function getDlink(session, shareid, uk, sign, timestamp, fsId, surl = "") {
  const params = new URLSearchParams({
    app_id: "250528",
    channel: "dubox",
    clienttype: "0",
    web: "1",
    shareid: String(shareid),
    uk: String(uk),
    sign,
    timestamp: String(timestamp),
    fid_list: `[${fsId}]`,
    "dp-logid": logid(),
    type: "dlink",
  });
  const h = headers(session, surl);
  h["Accept"] = "application/json, text/plain, */*";
  h["Origin"] = BASE_URL;

  const resp = await fetch(`${BASE_URL}/api/filemetas?${params}`, { headers: h });
  let data;
  try {
    data = await resp.json();
  } catch {
    throw new TeraBoxError("filemetas did not return JSON (likely blocked/banned response)");
  }

  if (data.errno !== 0) {
    throw new TeraBoxError(
      `filemetas failed: errno=${data.errno} (dlink often needs premium cookie / VIP account)`
    );
  }

  const infoList = data.info || [];
  if (!infoList.length || !infoList[0].dlink) {
    throw new TeraBoxError("No dlink in filemetas response (not available for this file/account)");
  }
  return infoList[0].dlink;
}

export async function resolveDownload(env, rawUrl, preferredQuality = "M3U8_AUTO_720") {
  const base = await resolveTeraboxLink(rawUrl);
  const session = makeSession(env);

  try {
    const dlink = await getDlink(session, base.shareid, base.uk, base.sign, base.timestamp, base.fs_id, base.surl);
    return {
      filename: base.filename,
      size: base.size,
      size_mb: base.size_mb,
      thumb: base.thumb,
      method: "direct_dlink",
      download_url: dlink,
    };
  } catch (dlinkErr) {
    const playlistData = await resolvePlaylist(env, rawUrl, preferredQuality);
    return {
      filename: base.filename,
      size: base.size,
      size_mb: base.size_mb,
      thumb: base.thumb,
      method: "fallback_playlist",
      fallback_reason: String(dlinkErr.message || dlinkErr),
      quality_used: playlistData.quality_used,
      chunks_found: playlistData.chunks_found,
      manifest: playlistData.manifest,
    };
  }
}
