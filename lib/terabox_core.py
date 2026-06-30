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
