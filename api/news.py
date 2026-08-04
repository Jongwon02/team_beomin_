# -*- coding: utf-8 -*-
"""GET /api/news/<작물>  (vercel.json rewrite → /api/news?crop=<작물>)

Beomin_web/news_server.py의 fetch_news()를 그대로 재사용한다. 그 모듈은
`if __name__ == "__main__"` 아래에서만 서버를 띄우므로 import는 안전하다.
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
    """rewrite로 붙은 쿼리(?crop=…)를 먼저 보고, 없으면 경로 마지막 조각을 쓴다."""
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
        # 서버 인메모리 캐시는 서버리스에서 인스턴스마다 흩어지므로, CDN 캐시로 보완한다.
        # (news_server.CACHE_TTL = 1200초와 같은 20분)
        self.send_header("Cache-Control",
                         f"public, s-maxage={max_age}, stale-while-revalidate=60" if max_age else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        crop = param(self.path, "crop")
        if not crop:
            return self._send(404, {"error": "use /api/news/<crop>"})
        if not news_server.CID or not news_server.CSECRET:
            return self._send(500, {"error": "NAVER_NEWS_CLIENT_ID/SECRET 환경변수가 없습니다"})
        try:
            return self._send(200, news_server.fetch_news(crop), max_age=1200)
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"error": str(e)})

    def log_message(self, *a):
        pass
