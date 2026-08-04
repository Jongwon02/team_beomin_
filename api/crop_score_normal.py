# -*- coding: utf-8 -*-
"""GET /api/crop-score-normal/<작물>?region=<지역>
   (rewrite → /api/crop_score_normal?crop=<작물>)

평년(여러 해 통계) 기준 적합도. 홈 화면과 작물 상세의 "적합도"가 이 값을 쓴다.
실시간 예보/실측을 쓰는 /api/crop-score 와 달리 **접속 시점에 따라 등급이 바뀌지 않는다.**

heeyeon2026 브랜치가 crop_score_server.py(:8002)에는 이 라우트를 넣었지만 api/ 함수와
vercel.json rewrite가 없어서 **배포에서는 404 → 정적 등급 폴백**이 되고 있었다.
그러면 평년 기준으로 바꾼 의미가 프로덕션에서 사라진다.

빠르다: data/processed/climate_normal_scores.json 을 읽을 뿐 공공 API를 타지 않는다.
그래서 CDN 캐시도 길게 준다(평년값은 배치로만 갱신된다).
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

# crop_score_server가 backend 하위 폴더를 sys.path에 등록하고 os.chdir까지 해준다.
import crop_score_server as css  # noqa: E402

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
        region = urllib.parse.unquote(qs.get("region", [""])[0]).strip()

        if crop not in css.CROPS:
            return self._send(400, {"error": f"지원하지 않는 작물명입니다: '{crop}'"})
        if not region:
            return self._send(400, {"error": "region 파라미터가 필요합니다 (예: ?region=충주시)"})
        try:
            payload = css.build_normal(crop, region)
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"error": f"점수 산출 실패: {e}"})
        # 지역을 못 찾은 응답(status != matched)은 캐시하지 않는다.
        matched = isinstance(payload, dict) and payload.get("status") == "matched"
        return self._send(200, payload, max_age=CACHE_SECONDS if matched else 0)

    def log_message(self, *a):
        pass
