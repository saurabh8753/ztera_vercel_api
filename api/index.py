"""
ZTERA TeraBox API — Vercel serverless entrypoint.
Endpoints:
  GET /api/info?url=<terabox_share_url>
  GET /api/health
"""

import sys
import os
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "lib"))

from flask import Flask, request, jsonify, Response
from terabox_core import resolve_terabox_link, resolve_playlist, resolve_download, TeraBoxError

app = Flask(__name__)


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/api/health", methods=["GET"])
def health():
    return _cors(jsonify({"status": "ok", "service": "ZTERA TeraBox API"}))


@app.route("/api/info", methods=["GET", "OPTIONS"])
def info():
    if request.method == "OPTIONS":
        return _cors(app.make_default_options_response())

    url = request.args.get("url", "").strip()
    if not url:
        return _cors(jsonify({"success": False, "error": "Missing 'url' query parameter"})), 400

    try:
        data = resolve_terabox_link(url)
        return _cors(jsonify({"success": True, **data}))
    except TeraBoxError as e:
        return _cors(jsonify({"success": False, "error": str(e)})), 502
    except Exception as e:
        traceback.print_exc()
        return _cors(jsonify({"success": False, "error": f"Internal error: {str(e)}"})), 500


@app.route("/api/playlist", methods=["GET", "OPTIONS"])
def playlist():
    """
    Returns a REAL, fully playable M3U8 manifest (not TeraBox's raw single-chunk
    endpoint). Use this URL directly in HLS.js / video.js as the source.

    Query params:
      url      - required, terabox share link
      quality  - optional, default M3U8_AUTO_720
                 (M3U8_AUTO_1080 / 720 / 480 / 360)
      format   - optional, 'json' (default) or 'm3u8' (raw manifest, playable directly)
    """
    if request.method == "OPTIONS":
        return _cors(app.make_default_options_response())

    url = request.args.get("url", "").strip()
    quality = request.args.get("quality", "M3U8_AUTO_720").strip()
    out_format = request.args.get("format", "json").strip().lower()

    if not url:
        return _cors(jsonify({"success": False, "error": "Missing 'url' query parameter"})), 400

    try:
        data = resolve_playlist(url, preferred_quality=quality)
        if out_format == "m3u8":
            resp = Response(data["manifest"], mimetype="application/vnd.apple.mpegurl")
            return _cors(resp)
        return _cors(jsonify({"success": True, **data}))
    except TeraBoxError as e:
        return _cors(jsonify({"success": False, "error": str(e)})), 502
    except Exception as e:
        traceback.print_exc()
        return _cors(jsonify({"success": False, "error": f"Internal error: {str(e)}"})), 500


@app.route("/api/download", methods=["GET", "OPTIONS"])
def download():
    """
    Tries to return a single direct CDN download URL (dlink) — no chunking,
    fastest option for a "Download" button. If TeraBox doesn't expose dlink
    for this file/account, automatically falls back to the chunked playlist
    (method field tells you which one you got: "direct_dlink" or "fallback_playlist").
    """
    if request.method == "OPTIONS":
        return _cors(app.make_default_options_response())

    url = request.args.get("url", "").strip()
    quality = request.args.get("quality", "M3U8_AUTO_720").strip()

    if not url:
        return _cors(jsonify({"success": False, "error": "Missing 'url' query parameter"})), 400

    try:
        data = resolve_download(url, preferred_quality=quality)
        return _cors(jsonify({"success": True, **data}))
    except TeraBoxError as e:
        return _cors(jsonify({"success": False, "error": str(e)})), 502
    except Exception as e:
        traceback.print_exc()
        return _cors(jsonify({"success": False, "error": f"Internal error: {str(e)}"})), 500


# Vercel Python runtime detects this `app` object automatically.
