# -*- coding: utf-8 -*-
"""GET /api/health — 배포된 함수가 살아있는지 + 필요한 환경변수가 등록됐는지 확인.

키 '값'은 절대 응답에 담지 않는다. 존재 여부(true/false)만 내려준다.
"""
import json
import os
from http.server import BaseHTTPRequestHandler

REQUIRED = [
    "ANTHROPIC_API_KEY",
    "KMA_SERVICE_KEY",
    "ASOS_DALY_SERVICE_KEY",
    "SOIL_EXAM_STAT_SERVICE_KEY",
    "SOIL_EXAM_LIST_SERVICE_KEY",
    "NAVER_NEWS_CLIENT_ID",
    "NAVER_NEWS_CLIENT_SECRET",
]


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        env = {k: bool(os.environ.get(k)) for k in REQUIRED}
        payload = {
            "ok": True,
            "env": env,
            "env_missing": [k for k, v in env.items() if not v],
            "supabase": {
                "url": bool(os.environ.get("SUPABASE_URL")),
                "service_role": bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY")),
            },
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass
