# -*- coding: utf-8 -*-
"""GET /api/cultivars/<작물>  (rewrite → /api/cultivars?crop=<작물>)

품종 목록·특성. 지역과 무관하므로 외부 API를 타지 않고 data/cultivars/<작물>.json만
읽는다 → 빠르고, CDN 캐시를 하루로 준다.

여기서 나가는 품종이 곧 **추천 가능 집합**이다. 작물 일반 지식(crops_for_llm.json)의
major_varieties는 이 목록에 넣지 않는다 - 감자만 24품종이 들어 있고 우리가 특성을
검수한 목록이 아니다(cultivar_data.is_recommendable 주석 참고).
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

import crop_score_server as css  # noqa: E402
import cultivar_api  # noqa: E402

CACHE_SECONDS = 24 * 60 * 60


class handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, max_age=0):
        body = json.dumps(css._json_safe(payload), ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",
                         f"public, s-maxage={max_age}, stale-while-revalidate=600" if max_age else "no-cache")
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

        if crop not in css.CROPS:
            return self._send(400, {"error": f"지원하지 않는 작물명입니다: '{crop}'"})
        try:
            payload = cultivar_api.cultivars_payload(crop)
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"error": f"품종 목록 조회 실패: {e}"})
        return self._send(200, payload, max_age=0 if payload.get("error") else CACHE_SECONDS)

    def log_message(self, *a):
        pass
