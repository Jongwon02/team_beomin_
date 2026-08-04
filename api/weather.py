# -*- coding: utf-8 -*-
"""GET /api/weather/<도>  (rewrite → /api/weather?province=<도>)

news_server.fetch_weather() 재사용. 기상청 ASOS 일자료는 하루 1회 갱신이므로
CDN 캐시를 3시간(WEATHER_TTL과 동일)으로 준다.
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
                         f"public, s-maxage={max_age}, stale-while-revalidate=300" if max_age else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        province = param(self.path, "province")
        if not province:
            return self._send(404, {"error": "use /api/weather/<province>"})
        try:
            return self._send(200, news_server.fetch_weather(province), max_age=10800)
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"error": str(e)})

    def log_message(self, *a):
        pass
