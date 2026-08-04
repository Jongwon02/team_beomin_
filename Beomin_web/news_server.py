# -*- coding: utf-8 -*-
"""농업 뉴스 서버 (네이버 뉴스검색 API + 농업 필터 + 캐싱).

- 무설치: 파이썬 표준 라이브러리만 사용
- 실행:  python news_server.py   (기본 포트 8001)
- 호출:  GET http://localhost:8001/api/news/감자
- 키:    같은 폴더의 .env 에서 NAVER_NEWS_CLIENT_ID / _SECRET 읽음
"""
import os, re, sys, json, time, ssl, html, datetime, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
# 백엔드는 하위 폴더 간 bare import를 쓰므로 경로를 등록한다(crop_score_server.py와 같은 방식).
for _sub in ("api", "scoring", "services", "utils"):
    _p = os.path.join(PROJECT_DIR, "backend", _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
PORT = 8001
CACHE_TTL = 1200  # 20분

# ---- .env 로드 ----
def find_env():
    """이 폴더부터 상위로 올라가며 첫 .env 를 찾는다(프로젝트 루트의 통합 .env 지원)."""
    d = BASE_DIR
    while True:
        path = os.path.join(d, ".env")
        if os.path.exists(path):
            return path
        parent = os.path.dirname(d)
        if parent == d:          # 루트 도달
            return os.path.join(BASE_DIR, ".env")  # 없으면 기존 기본 경로
        d = parent

def load_env():
    env = {}
    path = find_env()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

ENV = load_env()
CID = ENV.get("NAVER_NEWS_CLIENT_ID", "")
CSECRET = ENV.get("NAVER_NEWS_CLIENT_SECRET", "")
NEWS_URL = ENV.get("NAVER_NEWS_ENDPOINT", "https://openapi.naver.com/v1/search/news.json")

# ---- 작물별 검색어 & 농업 필터 ----
CROP_QUERY = {
    "감자": "감자 재배", "오이": "오이 재배", "상추": "상추 재배",
    "배": "배 과수 농가", "사과": "사과 과수 농가",
}
AGRI_WORDS = ["농사", "재배", "수확", "농가", "출하", "시세", "병해충", "파종", "작황",
              "농업", "농촌", "과수", "밭", "수매", "도매", "농진청", "작물", "농민",
              "영농", "재해", "폭염", "가뭄", "장마", "생산량", "품종", "농협", "농식품"]
BLACKLIST = ["레시피", "맛집", "드라마", "아이돌", "요리법", "다이어트", "여행", "게임",
             "냉면", "장학", "봉사", "라이온스", "정형외과", "사과문", "사과드", "사과했", "사과와 함께"]

_cache = {}   # crop -> (ts, data)
_ctx = ssl.create_default_context(); _ctx.check_hostname = False; _ctx.verify_mode = ssl.CERT_NONE

# ---- 농사 계획 캘린더용 실시간 날씨 (기상청 ASOS 일자료) ----
# CropAdvisor의 '내 농사 계획' 체크리스트가 GET /api/weather/<도> 로 최근 실측 기상을
# 요청한다(저온·가뭄·장마·고온 경고 판정용). 도별 대표 종관관측소는
# data/processed/region_cluster_map.json 에 실재하는 지점으로만 골랐다.
ASOS_URL = "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
ASOS_KEY = ENV.get("ASOS_DALY_SERVICE_KEY", "") or ENV.get("KMA_SERVICE_KEY", "")
PROVINCE_STATION = {
    "경기도": (203, "이천"), "강원도": (114, "원주"),
    "충청북도": (131, "청주"), "충청남도": (232, "천안"),
    "전라북도": (146, "전주"), "전라남도": (156, "광주"),
    "경상북도": (136, "안동"), "경상남도": (192, "진주"),
    "제주도": (184, "제주"),
    # 특별시/광역시/특별자치시 - 도(道)와 달리 도시 자체가 종관관측소를 갖고 있어
    # 그 도시의 ASOS 지점을 그대로 쓴다. 실제 API로 지점번호-지점명이 일치함을 확인했다.
    "서울특별시": (108, "서울"), "인천광역시": (112, "인천"),
    "대전광역시": (133, "대전"), "대구광역시": (143, "대구"),
    "광주광역시": (156, "광주"), "부산광역시": (159, "부산"),
    "울산광역시": (152, "울산"), "세종특별자치시": (239, "세종"),
}
WEATHER_DAYS = 14          # 프런트 summarizeWeather()가 최근 14일 누적/극값을 본다
WEATHER_TTL = 3 * 3600     # 일자료는 하루 1회만 갱신되므로 3시간 캐시(프런트도 3시간 캐시)
_weather_cache = {}        # province -> (ts, days)


def _f(v):
    """ASOS 응답의 결측('', ' ', '-')을 None으로, 나머지는 float으로."""
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def fetch_weather(province):
    """도(province) 대표 관측소의 최근 WEATHER_DAYS일 일자료. 실패/미지원 도는 빈 리스트."""
    st = PROVINCE_STATION.get(province)
    if not st or not ASOS_KEY:
        return []
    now = time.time()
    if province in _weather_cache and now - _weather_cache[province][0] < WEATHER_TTL:
        return _weather_cache[province][1]

    stn_id, stn_name = st
    # 일자료는 전날까지만 확정 제공되므로 어제를 끝으로 조회한다.
    end = datetime.date.today() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=WEATHER_DAYS - 1)
    qs = urllib.parse.urlencode({
        "serviceKey": ASOS_KEY, "pageNo": 1, "numOfRows": WEATHER_DAYS + 5,
        "dataType": "JSON", "dataCd": "ASOS", "dateCd": "DAY",
        "startDt": start.strftime("%Y%m%d"), "endDt": end.strftime("%Y%m%d"), "stnIds": stn_id,
    })
    with urllib.request.urlopen(ASOS_URL + "?" + qs, context=_ctx, timeout=15) as r:
        raw = json.load(r)
    items = (((raw.get("response") or {}).get("body") or {}).get("items") or {}).get("item") or []
    days = []
    for it in items:
        days.append({
            "date": it.get("tm", ""),
            "stnName": it.get("stnNm") or stn_name,
            "avgTa": _f(it.get("avgTa")), "maxTa": _f(it.get("maxTa")), "minTa": _f(it.get("minTa")),
            "sumRn": _f(it.get("sumRn")) or 0.0,   # 강수 없는 날은 빈 값으로 오므로 0으로 채움
        })
    days.sort(key=lambda d: d["date"])
    _weather_cache[province] = (now, days)
    return days

# ---- 주간(단기+중기) 예보: GET /api/weekly/<지역 전체 이름> ----------------
# 프로필의 '실시간 반영 정보'가 앞으로 7일을 보려면 단기예보(+0~+3일)와
# 중기예보(+4일~)를 이어 붙여야 한다. 두 API를 프런트에서 직접 부르면 인증키가
# 브라우저에 노출되므로 여기서 중계한다.
WEEKLY_TTL = 3 * 3600            # 중기예보는 하루 2회(06/18시) 발표라 3시간 캐시로 충분
WEEKLY_TTL_PARTIAL = 300         # 일부 날짜가 빈 결과는 3시간 붙잡아두면 안 되므로 5분만
_weekly_cache = {}               # region_full_name -> (ts, payload)


def fetch_weekly(region_name):
    from region_mapper import find_nearest_station          # noqa: E402
    from midfcst_regions import lookup_by_name              # noqa: E402
    from weekly_fcst import get_weekly_forecast             # noqa: E402

    now = time.time()
    hit = _weekly_cache.get(region_name)
    if hit:
        # 단기예보 타임아웃 등으로 앞쪽 날짜가 빈 결과는 짧게만 재사용한다.
        ttl = WEEKLY_TTL_PARTIAL if hit[1].get("missing") else WEEKLY_TTL
        if now - hit[0] < ttl:
            return hit[1]

    codes = lookup_by_name(region_name)
    if not codes:
        return {"error": "예보구역을 찾지 못했어요: %s" % region_name}
    m = find_nearest_station(region_name)
    if m.get("status") != "matched":
        return {"error": "지역을 찾지 못했어요: %s (%s)" % (region_name, m.get("status"))}
    reg = m["matched_region"]

    # 화면은 항상 7일치만 보여주지만, 캘린더에서 오늘이 아닌 날짜를 골랐을 때도 그 날짜
    # 기준 7일을 잘라 보여줘야 하므로 중기예보가 주는 최장 범위(+10일)까지 미리 받아둔다.
    data = get_weekly_forecast(reg["lat"], reg["lon"], codes["land"], codes["ta"], days=11)
    data["region"] = region_name
    data["taVia"] = codes.get("taVia")        # 대표도시로 대체한 경우 어디 기준인지
    data["taKm"] = codes.get("taKm")
    _weekly_cache[region_name] = (now, data)
    return data


def clean(t):
    t = re.sub(r"<[^>]+>", "", t)          # 태그 제거
    return html.unescape(t).strip()

def relevant(item, crop):
    title = item["title"]; text = title + " " + item["desc"]
    if any(b in text for b in BLACKLIST):
        return False
    if crop not in title:            # 작물명이 제목에 있어야 (관련성↑)
        return False
    return any(w in text for w in AGRI_WORDS)   # 농업 맥락 단어 포함

def fmt_date(pub):  # 'Thu, 23 Jul 2026 10:00:00 +0900' -> '2026-07-23'
    m = re.search(r"(\d{1,2}) (\w{3}) (\d{4})", pub)
    if not m: return pub[:16]
    mon = {"Jan":"01","Feb":"02","Mar":"03","Apr":"04","May":"05","Jun":"06",
           "Jul":"07","Aug":"08","Sep":"09","Oct":"10","Nov":"11","Dec":"12"}.get(m.group(2), "01")
    return f"{m.group(3)}-{mon}-{int(m.group(1)):02d}"

def fetch_news(crop):
    now = time.time()
    if crop in _cache and now - _cache[crop][0] < CACHE_TTL:
        return _cache[crop][1]
    q = urllib.parse.quote(CROP_QUERY.get(crop, crop + " 재배"))
    url = f"{NEWS_URL}?query={q}&display=30&sort=date"
    req = urllib.request.Request(url, headers={
        "X-Naver-Client-Id": CID, "X-Naver-Client-Secret": CSECRET})
    with urllib.request.urlopen(req, context=_ctx, timeout=10) as r:
        raw = json.load(r)
    items = []
    for i in raw.get("items", []):
        it = {"title": clean(i.get("title", "")), "desc": clean(i.get("description", "")),
              "link": i.get("link", ""), "date": fmt_date(i.get("pubDate", ""))}
        items.append(it)
    agri = [{"title": i["title"], "link": i["link"], "date": i["date"]}
            for i in items if relevant(i, crop)][:6]
    _cache[crop] = (now, agri)
    return agri

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")  # 정적 사이트(8000)에서 호출 허용
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        w = re.match(r"^/api/weather/(.+)$", path)
        if w:
            province = urllib.parse.unquote(w.group(1))
            try:
                return self._send(200, fetch_weather(province))
            except Exception as e:
                return self._send(502, {"error": str(e)})
        k = re.match(r"^/api/weekly/(.+)$", path)
        if k:
            region = urllib.parse.unquote(k.group(1))
            try:
                return self._send(200, fetch_weekly(region))
            except Exception as e:
                return self._send(502, {"error": str(e)})
        m = re.match(r"^/api/news/(.+)$", path)
        if not m:
            return self._send(404, {"error": "use /api/news/<crop>, /api/weather/<province>, /api/weekly/<region>"})
        crop = urllib.parse.unquote(m.group(1))
        if not CID or not CSECRET:
            return self._send(500, {"error": "NAVER_NEWS keys missing in .env"})
        try:
            return self._send(200, fetch_news(crop))
        except Exception as e:
            return self._send(502, {"error": str(e)})

    def log_message(self, *a):  # 콘솔 조용히
        pass

if __name__ == "__main__":
    print(f"농업 뉴스 서버 실행: http://localhost:{PORT}/api/news/감자")
    print(f"키 로드: ID={'OK' if CID else 'MISSING'} / SECRET={'OK' if CSECRET else 'MISSING'}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
