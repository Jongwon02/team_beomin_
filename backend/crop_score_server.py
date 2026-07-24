# -*- coding: utf-8 -*-
"""작물 적합도 점수 API 서버 (heeyeon 백엔드 → HTTP 래퍼).

- 무설치(표준 라이브러리) HTTP 서버. 단, 백엔드는 pandas/requests/python-dotenv 필요.
- 실행:  python backend/crop_score_server.py   (기본 포트 8002)
- 호출:  GET http://localhost:8002/api/crop-score/사과?region=충주시
- For_Frontend.md §3 스펙: 지역명 문자열 입력, grade/grade_label 매핑, {"error":...} 형식.
"""
import os, sys, json, time, re, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))            # .../m/backend
PROJECT_DIR = os.path.dirname(BASE_DIR)                          # .../m
# 백엔드는 하위 폴더 간 bare import(import asos, from region_mapper ...)를 쓰므로 경로 등록
for sub in ("api", "scoring", "services", "utils"):
    p = os.path.join(BASE_DIR, sub)
    if p not in sys.path:
        sys.path.insert(0, p)
# .env 는 프로젝트 루트에서 로드되도록 CWD 보정(백엔드 load_dotenv()가 상위 탐색)
os.chdir(PROJECT_DIR)

from live_scoring import get_live_score  # noqa: E402

PORT = 8002
CROPS = {"사과", "배", "오이", "감자", "상추"}
CACHE_TTL = 600  # 10분
_cache = {}      # (crop, region) -> (ts, payload)

# score → grade/grade_label (프론트 표시용 4단계, For_Frontend.md §3)
def grade_of(score):
    if score is None:
        return None, "산출 불가"
    if score >= 80: return "good", "우수"
    if score >= 60: return "normal", "양호"
    if score >= 40: return "caution", "주의"
    return "bad", "위험"

def build(crop, region):
    key = (crop, region)
    now = time.time()
    if key in _cache and now - _cache[key][0] < CACHE_TTL:
        return _cache[key][1]
    result = get_live_score(region, crop)
    if isinstance(result, dict):
        score = result.get("score") or result.get("total_score")
        result["score"] = score
        g, gl = grade_of(score)
        result.setdefault("grade", g)
        result.setdefault("grade_label", gl)
        result.setdefault("crop", crop)
        result.setdefault("region", region)
    _cache[key] = (now, result)
    return result

def _json_safe(obj):
    """json.dumps는 값(value)이 아닌 dict의 키(key)가 str/int/float/bool/None이
    아니면 default=str로도 못 살린다(get_live_score의 risk_signals.*.daily_extremes가
    {datetime.date: 값} 형태라 여기서 걸림) - 재귀적으로 모든 dict 키를 문자열로 바꾼다."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(_json_safe(payload), ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        m = re.match(r"^/api/crop-score/(.+)$", parsed.path)
        if not m:
            return self._send(404, {"error": "use /api/crop-score/<crop>?region=<name>"})
        crop = urllib.parse.unquote(m.group(1))
        qs = urllib.parse.parse_qs(parsed.query)
        region = (qs.get("region", [""])[0]).strip()
        if crop not in CROPS:
            return self._send(400, {"error": f"지원하지 않는 작물명입니다: '{crop}'"})
        if not region:
            return self._send(400, {"error": "region 파라미터가 필요합니다 (예: ?region=충주시)"})
        try:
            return self._send(200, build(crop, region))
        except Exception as e:
            return self._send(502, {"error": f"점수 산출 실패: {e}"})

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    print(f"작물 점수 서버 실행: http://localhost:{PORT}/api/crop-score/사과?region=충주시")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
