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
from region_mapper import find_nearest_station  # noqa: E402
from climate_normal_score import get_climate_normal_score  # noqa: E402
import cultivar_api  # noqa: E402  (품종 추천 - breed.md §7)

PORT = 8002
CROPS = {"사과", "배", "오이", "감자", "상추"}
CACHE_TTL = 600  # 10분
_cache = {}      # (crop, region) -> (ts, payload)
_normal_cache = {}  # (crop, region) -> (ts, payload) - climate-normal 전용 캐시

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
        # 6개 기후 클러스터(전국 89개 관측소 기준, region_cluster_map.json) 중 이 지역이
        # 어디에 속하는지 붙여준다 - 프론트가 지역 설명·작물별 코멘트를 만드는 데 쓴다.
        # 작물별 관측소가 아니라 일반 최근접 관측소 기준이라 크롭과 무관하게 지역 하나당 값이 같다.
        try:
            cluster = find_nearest_station(region)
            if cluster.get("status") == "matched":
                result["cluster_id"] = cluster["station"]["cluster_id"]
                result["cluster_name"] = cluster["station"]["cluster_name"]
        except Exception:
            pass
    _cache[key] = (now, result)
    return result

def build_normal(crop, region):
    """실시간 예보/실측 대신 여러 해 평년 통계로 낸 안정적 적합도(climate_normal_score.py).
    접속 시점에 따라 등급이 뒤바뀌지 않아야 하는 홈/상세페이지 "적합도" 표시에 쓴다."""
    key = (crop, region)
    now = time.time()
    if key in _normal_cache and now - _normal_cache[key][0] < CACHE_TTL:
        return _normal_cache[key][1]
    result = get_climate_normal_score(region, crop)
    if isinstance(result, dict) and result.get("status") == "matched":
        g, gl = grade_of(result.get("total_score"))
        result.setdefault("grade", g)
        result.setdefault("grade_label", gl)
    _normal_cache[key] = (now, result)
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
        qs = urllib.parse.parse_qs(parsed.query)
        path = parsed.path

        # ── 품종 추천 (breed.md §7) ──────────────────────────────────────
        m = re.match(r"^/api/cultivars/(.+)$", path)
        if m:
            crop = urllib.parse.unquote(m.group(1))
            if crop not in CROPS:
                return self._send(400, {"error": f"지원하지 않는 작물명입니다: '{crop}'"})
            try:
                return self._send(200, cultivar_api.cultivars_payload(crop))
            except Exception as e:
                return self._send(502, {"error": f"품종 목록 조회 실패: {e}"})

        m = re.match(r"^/api/cultivar-score/(.+)$", path)
        if m:
            crop = urllib.parse.unquote(m.group(1))
            region = (qs.get("region", [""])[0]).strip()
            experience = (qs.get("experience", ["beginner"])[0]).strip() or "beginner"
            if crop not in CROPS:
                return self._send(400, {"error": f"지원하지 않는 작물명입니다: '{crop}'"})
            if not region:
                return self._send(400, {"error": "region 파라미터가 필요합니다 (예: ?region=충주시)"})
            try:
                # 작물 점수는 이미 캐시에 있을 때만 함께 싣는다(새로 계산하면 수초 더 걸린다).
                # 화면이 "감자 93.7점 → 그중 수미 77.4점"으로 이을 수 있게 작물 점수를
                # 함께 싣는다. **평년 기준**을 쓴다 - 화면·챗봇의 적합도가 모두 평년으로
                # 통일됐으므로 여기서 실시간 점수를 실으면 같은 화면에 두 기준이 섞인다.
                # (예전에는 실시간 _cache 를 읽었는데, 아무도 /api/crop-score 를 부르지
                #  않게 된 뒤로는 늘 비어 있어 작물점수가 아예 실리지 않았다.)
                crop_score = None
                try:
                    ns = build_normal(crop, region)   # JSON만 읽어 빠르다
                    if isinstance(ns, dict) and ns.get("status") == "matched":
                        crop_score = {"score": ns.get("total_score"),
                                      "grade_label": ns.get("grade_label")}
                except Exception as e:
                    # 이 파일은 logging 을 쓰지 않는다(표준 라이브러리 서버). 작물점수는
                    # 부가 정보이므로 실패해도 품종 응답은 그대로 내보낸다.
                    print(f"[cultivar-score] 작물 평년점수 조회 실패: {e}", file=sys.stderr)
                return self._send(200, cultivar_api.score_payload(
                    crop, region, experience=experience, crop_score=crop_score))
            except Exception as e:
                return self._send(502, {"error": f"품종 점수 산출 실패: {e}"})

        m = re.match(r"^/api/cultivar-profile/([^/]+)/(.+)$", path)
        if m:
            crop = urllib.parse.unquote(m.group(1))
            name = urllib.parse.unquote(m.group(2))
            topic = (qs.get("topic", [""])[0]).strip() or None
            try:
                return self._send(200, cultivar_api.profile_payload(crop, name, topic))
            except Exception as e:
                return self._send(502, {"error": f"품종 상세 조회 실패: {e}"})

        m = re.match(r"^/api/cultivar-report/([^/]+)/(.+)$", path)
        if m:
            crop = urllib.parse.unquote(m.group(1))
            name = urllib.parse.unquote(m.group(2))
            section = (qs.get("section", [""])[0]).strip() or None
            try:
                return self._send(200, cultivar_api.report_payload(crop, name, section))
            except Exception as e:
                return self._send(502, {"error": f"리포트 조회 실패: {e}"})

        # ── 작물 적합도 점수 - 평년(기후 클러스터/다년 통계) 기준, 접속 시점과 무관 ──
        m = re.match(r"^/api/crop-score-normal/(.+)$", path)
        if m:
            crop = urllib.parse.unquote(m.group(1))
            region = (qs.get("region", [""])[0]).strip()
            if crop not in CROPS:
                return self._send(400, {"error": f"지원하지 않는 작물명입니다: '{crop}'"})
            if not region:
                return self._send(400, {"error": "region 파라미터가 필요합니다 (예: ?region=충주시)"})
            try:
                return self._send(200, build_normal(crop, region))
            except Exception as e:
                return self._send(502, {"error": f"점수 산출 실패: {e}"})

        # ── 작물 적합도 점수 (실시간, 기존) ──────────────────────────────
        m = re.match(r"^/api/crop-score/(.+)$", path)
        if not m:
            return self._send(404, {"error": (
                "use /api/crop-score/<crop>?region=<name> · /api/crop-score-normal/<crop>?region=<name> · "
                "/api/cultivar-score/<crop>?region=<name> · "
                "/api/cultivars/<crop> · /api/cultivar-profile/<crop>/<cultivar> · "
                "/api/cultivar-report/<crop>/<cultivar>"
            )})
        crop = urllib.parse.unquote(m.group(1))
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
