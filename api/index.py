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

from flask import Flask, request, jsonify
from terabox_core import resolve_terabox_link, TeraBoxError

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


# Vercel Python runtime detects this `app` object automatically.
