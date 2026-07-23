# -*- coding: utf-8 -*-
"""농업 뉴스 서버 (네이버 뉴스검색 API + 농업 필터 + 캐싱).

- 무설치: 파이썬 표준 라이브러리만 사용
- 실행:  python news_server.py   (기본 포트 8001)
- 호출:  GET http://localhost:8001/api/news/감자
- 키:    같은 폴더의 .env 에서 NAVER_NEWS_CLIENT_ID / _SECRET 읽음
"""
import os, re, json, time, ssl, html, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8001
CACHE_TTL = 1200  # 20분

# ---- .env 로드 ----
def load_env():
    env = {}
    path = os.path.join(BASE_DIR, ".env")
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
        m = re.match(r"^/api/news/(.+)$", urllib.parse.urlparse(self.path).path)
        if not m:
            return self._send(404, {"error": "use /api/news/<crop>"})
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
