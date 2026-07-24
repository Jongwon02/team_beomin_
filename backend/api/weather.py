"""기상청 단기예보 조회서비스(공공데이터포털) 연동 모듈.

https://www.data.go.kr/data/15084084/openapi.do
엔드포인트: http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst
"""

import logging
import math
import os
import re
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ENDPOINT = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
REQUEST_TIMEOUT_SECONDS = 10
NUM_OF_ROWS = 1000  # totalCount가 보통 700~800이라 3일치를 한 번에 받으려면 크게 잡아야 함

FORECAST_CATEGORIES = {"TMP", "POP", "PCP", "SKY", "PTY", "WSD"}

# 단기예보 발표시각(일 8회). 실제 API 반영까지 통상 약 10분 지연이 있어 계산 시 감안한다.
BASE_TIMES = ["0200", "0500", "0800", "1100", "1400", "1700", "2000", "2300"]
BASE_TIME_PUBLISH_DELAY_MINUTES = 10

# 기상청 격자 변환 공식(LCC 도법) 계수 - 기상청 공식 문서 기준
_GRID_RE = 6371.00877  # 지구 반경(km)
_GRID_SIZE = 5.0  # 격자 간격(km)
_GRID_SLAT1 = 30.0  # 투영 위도1(degree)
_GRID_SLAT2 = 60.0  # 투영 위도2(degree)
_GRID_OLON = 126.0  # 기준점 경도(degree)
_GRID_OLAT = 38.0  # 기준점 위도(degree)
_GRID_XO = 43  # 기준점 X좌표(GRID)
_GRID_YO = 136  # 기준점 Y좌표(GRID)


def latlon_to_grid(lat, lon):
    """위경도(lat, lon)를 기상청 격자좌표(nx, ny)로 변환한다."""
    degrad = math.pi / 180.0
    re = _GRID_RE / _GRID_SIZE
    slat1 = _GRID_SLAT1 * degrad
    slat2 = _GRID_SLAT2 * degrad
    olon = _GRID_OLON * degrad
    olat = _GRID_OLAT * degrad

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = math.pow(sf, sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / math.pow(ro, sn)

    ra = math.tan(math.pi * 0.25 + lat * degrad * 0.5)
    ra = re * sf / math.pow(ra, sn)
    theta = lon * degrad - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    x = ra * math.sin(theta) + _GRID_XO
    y = ro - ra * math.cos(theta) + _GRID_YO

    nx = int(x + 0.5)
    ny = int(y + 0.5)
    return nx, ny


def get_latest_base_datetime(now=None):
    """현재 시각 기준 가장 최근에 발표되어 API에 반영됐을 base_date, base_time을 계산한다."""
    now = now or datetime.now()
    effective = now - timedelta(minutes=BASE_TIME_PUBLISH_DELAY_MINUTES)
    hhmm = effective.strftime("%H%M")

    candidates = [t for t in BASE_TIMES if t <= hhmm]
    if candidates:
        return effective.strftime("%Y%m%d"), candidates[-1]

    # 자정 직후 등 당일 첫 발표(02시)에도 못 미치면 전날 마지막 발표(23시)를 사용
    prev_day = effective - timedelta(days=1)
    return prev_day.strftime("%Y%m%d"), BASE_TIMES[-1]


def _parse_pcp(value):
    """PCP(강수량) 문자열("10.0mm", "1.0mm 미만", "강수없음")에서 숫자만 추출한다."""
    if value is None:
        return 0.0
    value = value.strip()
    if value in ("강수없음", "-", ""):
        return 0.0
    match = re.search(r"[\d.]+", value)
    return float(match.group()) if match else 0.0


def get_short_term_forecast(lat, lon, service_key=None):
    """위경도 기준 단기예보(3일치)를 조회해 날짜/시각별 딕셔너리로 반환한다.

    반환 형태 (실패 시 None):
    {
        "nx": int, "ny": int, "base_date": str, "base_time": str,
        "forecast": {
            "20260722": {
                "1400": {"TMP": "27", "POP": "30", "PCP": 0.0, "SKY": "3", "PTY": "0", "WSD": "2.1"},
                ...
            },
            ...
        },
    }
    """
    service_key = service_key or os.environ.get("KMA_SERVICE_KEY")
    if not service_key:
        logger.error("[weather] KMA_SERVICE_KEY 환경변수가 설정되어 있지 않습니다.")
        return None

    nx, ny = latlon_to_grid(lat, lon)
    base_date, base_time = get_latest_base_datetime()

    params = {
        "serviceKey": service_key,
        "pageNo": "1",
        "numOfRows": str(NUM_OF_ROWS),
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": nx,
        "ny": ny,
    }

    try:
        resp = requests.get(ENDPOINT, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        logger.error("[weather] 기상청 API 요청 타임아웃 (nx=%s, ny=%s)", nx, ny)
        return None
    except requests.exceptions.RequestException as e:
        logger.error("[weather] 기상청 API 요청 실패: %s", e)
        return None

    try:
        data = resp.json()
    except ValueError:
        logger.error("[weather] 응답을 JSON으로 파싱할 수 없습니다: %s", resp.text[:300])
        return None

    header = data.get("response", {}).get("header", {})
    result_code = header.get("resultCode")
    if result_code != "00":
        logger.error(
            "[weather] API 오류 응답: resultCode=%s, resultMsg=%s", result_code, header.get("resultMsg")
        )
        return None

    body = data.get("response", {}).get("body", {})
    items = body.get("items", {}).get("item", [])

    organized = {}
    for item in items:
        category = item.get("category")
        if category not in FORECAST_CATEGORIES:
            continue
        fcst_date = item.get("fcstDate")
        fcst_time = item.get("fcstTime")
        value = item.get("fcstValue")

        slot = organized.setdefault(fcst_date, {}).setdefault(fcst_time, {})
        slot[category] = _parse_pcp(value) if category == "PCP" else value

    return {
        "nx": nx,
        "ny": ny,
        "base_date": base_date,
        "base_time": base_time,
        "forecast": organized,
    }


def get_forecast_for_matched_region(region_match, service_key=None):
    """region_mapper.find_nearest_station()의 매칭 결과를 받아 바로 예보를 조회한다.

    region_match["matched_region"]["lat"/"lon"](사용자가 입력한 지역 자체의 좌표)를 사용한다 -
    기후 클러스터링용 관측소 좌표(station.lat/lon)보다 격자예보 정확도 측면에서 더 적합하다.
    """
    if not region_match or region_match.get("status") != "matched":
        logger.error("[weather] 유효한 매칭 결과가 아닙니다: %s", region_match)
        return None

    matched_region = region_match["matched_region"]
    return get_short_term_forecast(matched_region["lat"], matched_region["lon"], service_key=service_key)
