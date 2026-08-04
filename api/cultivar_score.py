# -*- coding: utf-8 -*-
"""GET /api/cultivar-score/<작물>?region=<지역>&experience=<beginner|...>
   (rewrite → /api/cultivar_score?crop=<작물>)

backend/crop_score_server.py의 :8002 라우트(같은 이름의 경로)와 같은 응답을 낸다.
계산은 backend/api/cultivar_api.score_payload가 작물에 따라 엔진을 골라서 한다.
  · 감자                → cultivar_fit (기후 점수)
  · 사과·배·오이·상추   → cultivar_conditions (조건 기반)

느린 이유: 감자 경로는 ASOS 일자료 10년 + 흙토람을 탄다 → vercel.json maxDuration 60초.
품종 판정은 과거 평년값만 쓰므로 사실상 정적이다 → CDN 캐시를 길게 준다.
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

# crop_score_server를 먼저 import한다. 이 모듈이 backend 하위 폴더(api/scoring/services/
# utils)를 sys.path에 등록하고 os.chdir까지 해준다 - 백엔드가 bare import를 쓰기 때문에
# 이 준비 없이는 cultivar_api가 cultivar_data를 찾지 못한다.
import crop_score_server as css  # noqa: E402
import cultivar_api  # noqa: E402

CACHE_SECONDS = 6 * 60 * 60


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
        region = urllib.parse.unquote(qs.get("region", [""])[0]).strip()
        experience = (qs.get("experience", ["beginner"])[0]).strip() or "beginner"

        if crop not in css.CROPS:
            return self._send(400, {"error": f"지원하지 않는 작물명입니다: '{crop}'"})
        if not region:
            return self._send(400, {"error": "region 파라미터가 필요합니다 (예: ?region=평창군)"})
        try:
            payload = cultivar_api.score_payload(crop, region, experience=experience)
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"error": f"품종 판정 실패: {e}"})
        # 데이터가 없는 작물은 error를 담아 200으로 온다 - 캐시하지 않는다.
        return self._send(200, payload, max_age=0 if payload.get("error") else CACHE_SECONDS)

    def log_message(self, *a):
        pass
