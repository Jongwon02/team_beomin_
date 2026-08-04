# -*- coding: utf-8 -*-
"""GET /api/crop-score/<작물>?region=<지역>  (rewrite → /api/crop_score?crop=<작물>)

backend/crop_score_server.py의 build()/grade_of()/_json_safe()를 그대로 재사용한다.
공공 API를 여러 개 직렬로 타므로 느리다 → vercel.json에서 maxDuration 60초.
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "backend"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# crop_score_server는 import 시 backend 하위 폴더를 sys.path에 등록하고
# os.chdir(PROJECT_DIR)까지 해준다(백엔드가 bare import를 쓰기 때문).
import crop_score_server as css  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, max_age=0):
        body = json.dumps(css._json_safe(payload), ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 인메모리 _cache(600초)는 서버리스에서 흩어지므로 CDN 캐시로 같은 TTL을 준다.
        self.send_header("Cache-Control",
                         f"public, s-maxage={max_age}, stale-while-revalidate=120" if max_age else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        crop = ""
        if qs.get("crop"):
            crop = urllib.parse.unquote(qs["crop"][0]).strip()
        else:
            tail = parsed.path.rstrip("/").rsplit("/", 1)
            if len(tail) > 1:
                crop = urllib.parse.unquote(tail[-1]).strip()
        region = urllib.parse.unquote((qs.get("region", [""])[0])).strip()

        if crop not in css.CROPS:
            return self._send(400, {"error": f"지원하지 않는 작물명입니다: '{crop}'"})
        if not region:
            return self._send(400, {"error": "region 파라미터가 필요합니다 (예: ?region=충주시)"})
        try:
            return self._send(200, css.build(crop, region), max_age=600)
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"error": f"점수 산출 실패: {e}"})

    def log_message(self, *a):
        pass
