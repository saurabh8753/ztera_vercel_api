"""
ZTERA TeraBox extraction core.
Ported from core_pipeline.py — Telegram, ffmpeg aur Chromium dependencies hatayi gayi hain.
Sirf metadata + signed streaming/download links nikalta hai (Vercel-friendly, fast, lightweight).
"""

import os
import re
import time
import random
import requests
from urllib.parse import unquote, urlparse, urlunparse, urlencode, parse_qs

BASE_DOMAIN = "dm.1024tera.com"
BASE_URL = f"https://{BASE_DOMAIN}"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

BYTES_PER_MB = 1048576


class TeraBoxError(Exception):
    """Raised for known, expected TeraBox errors."""


# ── Cookies (env se load, COOKIES1, COOKIES2, ...) ─────────────────────────

def _load_cookies_list() -> list:
    cookies = []
    for idx in range(1, 10):
        c = os.getenv(f"COOKIES{idx}")
        if c:
            cookies.append(c)
    return cookies


COOKIES_LIST = _load_cookies_list()


def load_session() -> requests.Session:
    session = requests.Session()
    if not COOKIES_LIST:
        raise TeraBoxError("No cookies configured on server (COOKIES1, COOKIES2... missing in env)")
    cookie_str = random.choice(COOKIES_LIST)
    for c in cookie_str.split(";"):
        if "=" in c:
            k, v = c.strip().split("=", 1)
            session.cookies.set(k.strip(), v.strip(), domain=".1024tera.com", path="/")
    return session


def _logid() -> str:
    return str(random.randint(400_000_000_000_000_000, 999_999_999_999_999_999))


def _cookie_str(session: requests.Session) -> str:
    return "; ".join(
        f"{c.name}={c.value}" for c in session.cookies
        if "1024tera" in (c.domain or "")
    )


def _headers(session: requests.Session, surl: str = "") -> dict:
    hdrs = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": f"{BASE_URL}/wap/share/filelist?surl={surl}" if surl else f"{BASE_URL}/wap/share/filelist",
    }
    cookie_str = _cookie_str(session)
    if cookie_str:
        hdrs["Cookie"] = cookie_str
    return hdrs


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)


# ── Surl extraction from any TeraBox-style URL ──────────────────────────────

def extract_surl(url: str) -> str:
    """
    Accepts a full TeraBox share URL or a raw surl and returns the bare surl token.
    Handles formats like:
      https://1024terabox.com/s/1AbCdEfGhIjK
      https://terabox.com/s/1AbCdEfGhIjK?xyz=1
      1AbCdEfGhIjK
    """
    url = url.strip()
    m = re.search(r"/s/([A-Za-z0-9_-]+)", url)
    if m:
        token = m.group(1)
    else:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "surl" in qs:
            token = qs["surl"][0]
        else:
            token = url  # assume raw surl was passed directly

    # TeraBox surl tokens are commonly prefixed with "1" internally; strip it if present on /s/ links
    if token.startswith("1") and len(token) > 1:
        return token[1:]
    return token


# ── jsToken + share info ────────────────────────────────────────────────────

def get_js_token(session: requests.Session, surl: str) -> str:
    url = f"{BASE_URL}/wap/share/filelist?surl={surl}&clearCache=1"
    last_err = "Unknown error"
    for attempt in range(3):
        try:
            html = session.get(url, headers=_headers(session, surl), timeout=20).text

            m = re.search(r'fn%28%22([A-Fa-f0-9]+)%22%29', html)
            if m:
                return m.group(1)

            m = re.search(r'eval\(decodeURIComponent\(`([^`]+)`\)\)', html)
            if m:
                m2 = re.search(r'fn\("([A-Fa-f0-9]+)"\)', unquote(m.group(1)))
                if m2:
                    return m2.group(1)

            last_err = "Token patterns not found in HTML"
        except requests.RequestException as e:
            last_err = str(e)

        if attempt < 2:
            time.sleep(1)

    raise TeraBoxError(f"Could not extract jsToken after 3 attempts: {last_err}")


def get_share_info(session: requests.Session, js_token: str, surl: str) -> dict:
    params = {
        "app_id": "250528", "shorturl": f"1{surl}", "root": "1",
        "web": "1", "channel": "dubox", "clienttype": "0",
        "jsToken": js_token, "t": str(int(time.time())), "dp-logid": _logid(),
    }
    hdrs = _headers(session, surl)
    hdrs.update({"Accept": "application/json, text/plain, */*", "Origin": BASE_URL})
    resp = session.get(f"{BASE_URL}/api/shorturlinfo", params=params, headers=hdrs, timeout=20)
    data = resp.json()
    if data.get("errno") != 0:
        raise TeraBoxError(f"shorturlinfo failed: errno={data.get('errno')}")
    return data


def build_streaming_url(shareid, uk, sign, timestamp, fs_id, quality: str) -> str:
    return f"{BASE_URL}/share/streaming?" + urlencode({
        "uk": str(uk), "shareid": str(shareid), "type": quality,
        "fid": str(fs_id), "sign": sign, "timestamp": str(timestamp),
        "jsToken": "", "esl": "1", "isplayer": "1", "ehps": "1",
        "clienttype": "0", "app_id": "250528", "web": "1",
        "channel": "dubox", "dp-logid": _logid(),
    })


def get_first_chunk_cdn_url(session: requests.Session, shareid, uk, sign, timestamp, fs_id, quality: str, surl: str = "") -> str:
    """
    Streaming endpoint ko ek baar poll karke us response mein mile signed CDN .ts chunk
    URL ka 'base' return karta hai (player/downloader links banaane ke kaam aata hai).
    """
    url = build_streaming_url(shareid, uk, sign, timestamp, fs_id, quality)
    text = session.get(url, headers=_headers(session, surl), timeout=20).text.strip()
    if not text.startswith("#EXTM3U"):
        raise TeraBoxError("Streaming endpoint did not return a valid M3U8 playlist (cookie may be expired/banned)")
    return text


# ── Real chunk discovery (TeraBox's /share/streaming returns ONE random
#    chunk per request — we must poll repeatedly to collect them all) ───────

def discover_all_hls_chunks(session: requests.Session, shareid, uk, sign, timestamp,
                             fs_id, quality: str, surl: str = "",
                             max_budget: int = 40, time_budget_sec: float = 18.0) -> list:
    """
    Polls the streaming endpoint repeatedly to discover every unique TS chunk.
    Budget kept low (vs the original bot) to fit inside Vercel's execution timeout.
    Returns a list of (chunk_idx, signed_cdn_url, ts_size) sorted by idx.
    Returns [] if this quality isn't available at all (e.g. not transcoded for this file).
    """
    start_t = time.time()
    known = {}
    req_count = 0
    no_new_streak = 0
    max_idx = -1

    while True:
        if req_count >= max_budget or (time.time() - start_t) > time_budget_sec:
            break

        req_count += 1
        url = build_streaming_url(shareid, uk, sign, timestamp, fs_id, quality)
        try:
            text = session.get(url, headers=_headers(session, surl), timeout=15).text.strip()
        except requests.RequestException:
            time.sleep(0.5)
            continue

        if not text.startswith("#EXTM3U"):
            time.sleep(0.3)
            continue

        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parsed = urlparse(line)
            p = parse_qs(parsed.query, keep_blank_values=True)
            ts_size = int(p.get("ts_size", ["0"])[0])
            if ts_size <= 0:
                continue
            m = re.search(r'_(\d+)_ts/', parsed.path)
            if not m:
                continue
            idx = int(m.group(1))
            if idx in known:
                continue
            p["range"] = [f"0-{ts_size - 1}"]
            p["len"] = [str(ts_size)]
            full_url = urlunparse(parsed._replace(query=urlencode({k: v[0] for k, v in p.items()})))
            known[idx] = (idx, full_url, ts_size)

        cur_max = max(known.keys()) if known else -1
        if cur_max > max_idx:
            max_idx = cur_max
            no_new_streak = 0
        else:
            no_new_streak += 1

        if known and min(known) <= 1 and len(known) == (max(known) - min(known) + 1):
            if no_new_streak >= 6:
                break

        time.sleep(0.2)

    if not known:
        return []

    return [known[i] for i in sorted(known)]


def build_synthetic_m3u8(chunks: list, seg_duration: float = 4.0) -> str:
    """
    chunks: list of (idx, signed_cdn_url, ts_size) from discover_all_hls_chunks.
    Builds a standards-compliant M3U8 manifest pointing directly at TeraBox's
    real signed CDN .ts URLs, so HLS.js (or any HLS player) can play the FULL
    video instead of the single random chunk TeraBox's raw endpoint returns.
    """
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{int(seg_duration) + 1}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    for _, url, _ in chunks:
        lines.append(f"#EXTINF:{seg_duration:.3f},")
        lines.append(url)
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines)


def resolve_playlist(raw_url: str, preferred_quality: str = "M3U8_AUTO_720") -> dict:
    """
    Full flow: metadata -> discover real chunks -> build a playable M3U8.
    Auto-falls back through QUALITIES if the preferred one has 0 chunks
    (not transcoded / not available for this file or account).
    """
    base = resolve_terabox_link(raw_url)
    session = load_session()

    quality_order = [preferred_quality] + [q for q in QUALITIES if q != preferred_quality]

    for q in quality_order:
        chunks = discover_all_hls_chunks(
            session, base["shareid"], base["uk"], base["sign"], base["timestamp"],
            base["fs_id"], q, surl=base["surl"],
        )
        if chunks:
            manifest = build_synthetic_m3u8(chunks)
            return {
                "filename": base["filename"],
                "size": base["size"],
                "size_mb": base["size_mb"],
                "thumb": base["thumb"],
                "quality_used": q,
                "chunks_found": len(chunks),
                "manifest": manifest,
            }

    raise TeraBoxError(
        "No playable chunks found at any quality — cookie likely expired/banned, "
        "or this file isn't transcoded for streaming on this account."
    )


# ── Direct CDN download link (dlink) — single mp4 URL, not chunked ─────────
#
# NOTE: TeraBox doesn't expose `dlink` on the public `/api/shorturlinfo` call.
# It requires a separate authenticated call to `/api/filemetas`. This is
# reverse-engineered from observed traffic and is less stable than the
# streaming endpoints:
#   - Often IP-bound (the IP that requested it must be the one that fetches it)
#   - Frequently restricted to premium/VIP cookies — non-premium accounts may
#     get errno != 0 even when streaming works fine
#   - Short-lived (typically a few hours)
# Because of this, treat it as a "best effort, may not always work" path and
# always have the chunked playlist as a fallback.

def get_dlink(session: requests.Session, shareid, uk, sign, timestamp, fs_id, surl: str = "") -> str:
    params = {
        "app_id": "250528", "channel": "dubox", "clienttype": "0",
        "web": "1", "shareid": str(shareid), "uk": str(uk),
        "sign": sign, "timestamp": str(timestamp),
        "fid_list": f"[{fs_id}]", "dp-logid": _logid(), "type": "dlink",
    }
    hdrs = _headers(session, surl)
    hdrs.update({"Accept": "application/json, text/plain, */*", "Origin": BASE_URL})

    resp = session.get(f"{BASE_URL}/api/filemetas", params=params, headers=hdrs, timeout=20)
    try:
        data = resp.json()
    except ValueError:
        raise TeraBoxError("filemetas did not return JSON (likely blocked/banned response)")

    if data.get("errno") != 0:
        raise TeraBoxError(f"filemetas failed: errno={data.get('errno')} (dlink often needs premium cookie / VIP account)")

    info_list = data.get("info", [])
    if not info_list or not info_list[0].get("dlink"):
        raise TeraBoxError("No dlink in filemetas response (not available for this file/account)")

    return info_list[0]["dlink"]


def resolve_download(raw_url: str, preferred_quality: str = "M3U8_AUTO_720") -> dict:
    """
    Tries to get a direct single-file CDN dlink first (fast, no chunking).
    If unavailable/fails, automatically falls back to building a full
    chunked M3U8 playlist (always works if cookies are valid, just slower).
    """
    base = resolve_terabox_link(raw_url)
    session = load_session()

    try:
        dlink = get_dlink(
            session, base["shareid"], base["uk"], base["sign"], base["timestamp"],
            base["fs_id"], surl=base["surl"],
        )
        return {
            "filename": base["filename"],
            "size": base["size"],
            "size_mb": base["size_mb"],
            "thumb": base["thumb"],
            "method": "direct_dlink",
            "download_url": dlink,
        }
    except TeraBoxError as dlink_err:
        # Fallback: build a playable/downloadable chunked playlist instead
        playlist_data = resolve_playlist(raw_url, preferred_quality=preferred_quality)
        return {
            "filename": base["filename"],
            "size": base["size"],
            "size_mb": base["size_mb"],
            "thumb": base["thumb"],
            "method": "fallback_playlist",
            "fallback_reason": str(dlink_err),
            "quality_used": playlist_data["quality_used"],
            "chunks_found": playlist_data["chunks_found"],
            "manifest": playlist_data["manifest"],
        }


# ── Public function: full metadata + links in one call ─────────────────────

QUALITIES = ["M3U8_AUTO_1080", "M3U8_AUTO_720", "M3U8_AUTO_480", "M3U8_AUTO_360"]


def resolve_terabox_link(raw_url: str) -> dict:
    """
    Main entry point. Given any TeraBox share URL, returns:
      filename, size (bytes), fs_id, shareid, uk, sign, timestamp, surl,
      streaming_urls: { quality: m3u8_proxy_url }
    Raises TeraBoxError on failure.
    """
    surl = extract_surl(raw_url)
    if not surl:
        raise TeraBoxError("Could not parse a valid surl from the given URL")

    session = requests.Session()

    js_token = get_js_token(session, surl)
    info = get_share_info(session, js_token, surl)

    files = info.get("list", [])
    if not files:
        raise TeraBoxError("No files found in this share")

    f = files[0]

    shareid = info["shareid"]
    uk = info["uk"]
    sign = info["sign"]
    timestamp = info["timestamp"]
    fs_id = f["fs_id"]

    streaming_urls = {
        q: build_streaming_url(shareid, uk, sign, timestamp, fs_id, q)
        for q in QUALITIES
    }

    return {
        "filename": f.get("server_filename", "video"),
        "size": int(f.get("size", 0)),
        "size_mb": round(int(f.get("size", 0)) / BYTES_PER_MB, 2),
        "thumb": (f.get("thumbs") or {}).get("url3") or (f.get("thumbs") or {}).get("url1"),
        "fs_id": fs_id,
        "shareid": shareid,
        "uk": uk,
        "sign": sign,
        "timestamp": timestamp,
        "surl": surl,
        "streaming_urls": streaming_urls,
    }
