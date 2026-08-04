# -*- coding: utf-8 -*-
"""GET /api/weekly/<지역 full name>  (rewrite → /api/weekly?region=<지역>)

news_server.fetch_weekly() 재사용. 중기예보는 하루 2회(06/18시) 발표.
일부 날짜가 빈 응답(missing)은 오래 붙잡아두면 안 되므로 CDN 캐시도 5분으로 줄인다
(원본 WEEKLY_TTL_PARTIAL = 300과 동일한 취지).
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "Beomin_web"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import news_server  # noqa: E402


def param(path, name):
    parsed = urllib.parse.urlparse(path)
    qs = urllib.parse.parse_qs(parsed.query)
    if qs.get(name):
        return urllib.parse.unquote(qs[name][0]).strip()
    tail = parsed.path.rstrip("/").rsplit("/", 1)
    return urllib.parse.unquote(tail[-1]).strip() if len(tail) > 1 else ""


class handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, max_age=0):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",
                         f"public, s-maxage={max_age}, stale-while-revalidate=60" if max_age else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        region = param(self.path, "region")
        if not region:
            return self._send(404, {"error": "use /api/weekly/<region>"})
        try:
            data = news_server.fetch_weekly(region)
            ttl = 300 if isinstance(data, dict) and data.get("missing") else 10800
            return self._send(200, data, max_age=ttl)
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"error": str(e)})

    def log_message(self, *a):
        pass
