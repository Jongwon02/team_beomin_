"""농경지화학성 통계정보(흙토람 SoilExamStat V2, 공공데이터포털 15144685) 연동 모듈.

https://www.data.go.kr/data/15144685/openapi.do
엔드포인트: https://apis.data.go.kr/1390802/SoilEnviron/SoilExamStat/V2/{operation}

⚠️ 이 API는 "평균값"을 직접 주지 않는다. 실측 확인 결과, pH/유기물/유효인산 각각
   "지목(논/밭/시설재배지/과수원)별 6개 구간(1~6)의 필지 면적 분포"만 반환한다
   (예: acid_Pfld3_Area = 밭의 pH 구간3 해당 면적). 그래서 여기서는 각 구간의
   대표값(구간 중앙값)을 면적으로 가중평균해 근사 평균값을 만든다.

   구간 경계값은 공식 문서(OPEN API기술명세서_농경지화학성 통계 정보V2_ver1.0.hwp,
   해커톤 폴더 - 스크린샷으로 원문 확인) 기준이며, pH·유효인산은 지목별로 구간
   경계가 다르다(유기물만 지목 공통):
     - pH: 논/밭/과수원 4.5↓,4.6~5.0,5.1~5.5,5.6~6.0,6.1~6.5,6.6↑ /
           시설 5.0↓,5.1~5.5,5.6~6.0,6.1~6.5,6.6~7.0,7.1↑
     - 유효인산: 논 50↓,51~100,101~150,151~200,201~250,251↑ /
                밭·과수원 200↓,201~300,301~400,401~500,501~600,601↑ /
                시설 400↓,401~800,801~1200,1201~1600,1601~2000,2001↑
     - 유기물(공통): 10↓,11~20,21~30,31~40,41~50,51↑
   ⚠️ 첫/마지막 구간은 원문에도 개방구간(이하/이상)이라 폭이 없다 - 인접 구간과 같은
   폭을 가정해 중앙값을 추정했다(예: pH "4.5이하"는 폭 0.5로 가정해 [4.0,4.5]의
   중앙값 4.25). 이 부분만 근사이고, 나머지 닫힌 구간 경계는 원문 그대로다.
"""

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from bjd_lookup import get_stdg_candidates
from reference_data import LAND_USE_CATEGORY
from soil_ec import get_ec  # 흙토람 토양검정정보(getSoilExamList) 기반 실측 EC 조회

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = "https://apis.data.go.kr/1390802/SoilEnviron/SoilExamStat/V2"
OPERATIONS = {
    "pH": "/getFarmExamPhInfo",
    "유기물": "/getFarmExamOmInfo",
    "유효인산": "/getFarmExamApInfo",
}
FIELD_PREFIX = {"pH": "acid", "유기물": "om", "유효인산": "vldpha"}
REQUEST_TIMEOUT_SECONDS = 10

# ⚠️ 이 서비스키는 ASOS 일자료/흙토람 EC API와 하루 호출한도를 공유한다(soil_ec.py
# 참고). 지역 하나에 pH·유기물·유효인산 3개 항목 × 시군구 안 법정동 코드 수만큼
# 연속 호출하다 보니 순간적으로 초당 요청수 제한(429)에 걸리기 쉽고, soil_ec.py처럼
# 재시도가 없으면 딱 한 번 걸린 항목만 통째로 결측 처리돼 pH는 성공, 유기물은 실패
# 하는 식의 들쭉날쭉한 결과가 난다. soil_ec.py와 동일하게 짧은 재시도 + 디스크 캐시로
# 대응한다(캐시는 stdg_cd 단위 원본 응답을 저장해 get_soil_variable과
# get_soil_variable_with_fallback이 같은 캐시를 공유하게 한다).
MAX_429_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.5

CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "soil_stat_cache.json"
SUCCESS_TTL = timedelta(hours=24)
FAILURE_TTL = timedelta(hours=3)

_disk_cache = None


def _load_disk_cache():
    global _disk_cache
    if _disk_cache is not None:
        return _disk_cache
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                _disk_cache = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("[soil] 캐시 파일을 읽지 못해 새로 시작합니다: %s", e)
            _disk_cache = {}
    else:
        _disk_cache = {}
    return _disk_cache


def _save_disk_cache():
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_disk_cache, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("[soil] 캐시 파일 저장 실패(메모리 캐시로만 계속 동작): %s", e)


def _read_cache_entry(cache_key):
    cache = _load_disk_cache()
    entry = cache.get(cache_key)
    if entry is None:
        return False, None
    fetched_at = datetime.fromisoformat(entry["fetched_at"])
    ttl = SUCCESS_TTL if entry["areas"] is not None else FAILURE_TTL
    if datetime.now() - fetched_at > ttl:
        return False, None
    return True, entry["areas"]


def _write_cache_entry(cache_key, areas):
    cache = _load_disk_cache()
    cache[cache_key] = {"areas": areas, "fetched_at": datetime.now().isoformat()}
    _save_disk_cache()

# 구간별 대표값(중앙값). 원문 구간 경계는 모듈 docstring 참고 - 개방구간(첫/마지막)만
# 인접 구간과 같은 폭을 가정해 추정했고, 나머지는 원문 경계값 그대로 계산한 중앙값이다.
# pH·유효인산은 지목(Rfld=논, Pfld=밭, Fruit=과수원, Fachs=시설)별로 구간이 달라 지목별
# dict로 두고, 유기물만 지목 공통이라 리스트 하나로 둔다.
_PH_MIDPOINTS_FIELD = [4.25, 4.8, 5.3, 5.8, 6.3, 6.85]  # 논/밭/과수원 공통
_PH_MIDPOINTS_FACHS = [4.75, 5.3, 5.8, 6.3, 6.8, 7.35]  # 시설

_AP_MIDPOINTS_RFLD = [25.0, 75.5, 125.5, 175.5, 225.5, 276.0]  # 논
_AP_MIDPOINTS_FIELD = [150.0, 250.5, 350.5, 450.5, 550.5, 651.0]  # 밭/과수원 공통
_AP_MIDPOINTS_FACHS = [200.0, 600.5, 1000.5, 1400.5, 1800.5, 2201.0]  # 시설

BIN_MIDPOINTS = {
    "pH": {
        "Rfld": _PH_MIDPOINTS_FIELD, "Pfld": _PH_MIDPOINTS_FIELD, "Fruit": _PH_MIDPOINTS_FIELD,
        "Fachs": _PH_MIDPOINTS_FACHS,
    },
    "유기물": [5.0, 15.5, 25.5, 35.5, 45.5, 56.0],  # 지목 공통
    "유효인산": {
        "Rfld": _AP_MIDPOINTS_RFLD,
        "Pfld": _AP_MIDPOINTS_FIELD, "Fruit": _AP_MIDPOINTS_FIELD,
        "Fachs": _AP_MIDPOINTS_FACHS,
    },
}


def _get_bin_midpoints(variable, category):
    """variable(pH/유기물/유효인산)+category(Rfld/Pfld/Fachs/Fruit) -> 구간 중앙값 리스트.

    유기물은 지목 공통이라 category를 무시한다.
    """
    entry = BIN_MIDPOINTS[variable]
    if isinstance(entry, dict):
        return entry[category]
    return entry


def _fetch_operation(variable, stdg_cd, service_key):
    cache_key = f"{variable}|{stdg_cd}"
    hit, cached_areas = _read_cache_entry(cache_key)
    if hit:
        return cached_areas

    areas = _fetch_operation_uncached(variable, stdg_cd, service_key)
    _write_cache_entry(cache_key, areas)
    return areas


def _fetch_operation_uncached(variable, stdg_cd, service_key):
    url = BASE_URL + OPERATIONS[variable]
    params = {"serviceKey": service_key, "pageNo": "1", "numOfRows": "10", "STDG_CD": stdg_cd}
    resp = None
    for attempt in range(MAX_429_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            if resp.status_code == 429:
                if attempt < MAX_429_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                logger.warning("[soil] %s 429 재시도 소진(STDG_CD=%s)", variable, stdg_cd)
                return None
            resp.raise_for_status()
            break
        except requests.exceptions.Timeout:
            logger.error("[soil] %s API 요청 타임아웃 (STDG_CD=%s)", variable, stdg_cd)
            return None
        except requests.exceptions.RequestException as e:
            logger.error("[soil] %s API 요청 실패: %s", variable, e)
            return None

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        logger.error("[soil] %s 응답을 XML로 파싱할 수 없습니다: %s", variable, resp.text[:300])
        return None

    result_code = root.findtext("./header/result_Code")
    if result_code != "00" and result_code != "200":
        logger.warning(
            "[soil] %s API 오류/무응답: STDG_CD=%s, result_Code=%s, msg=%s",
            variable, stdg_cd, result_code, root.findtext("./header/result_Msg"),
        )
        return None

    item = root.find("./body/items/item")
    if item is None:
        return None

    prefix = FIELD_PREFIX[variable]
    areas = {}
    for category in ("Rfld", "Pfld", "Fachs", "Fruit"):
        bins = []
        for i in range(1, 7):
            text = item.findtext(f"{prefix}_{category}{i}_Area")
            try:
                bins.append(float(text))
            except (TypeError, ValueError):
                bins.append(0.0)
        areas[category] = bins
    return areas


def _weighted_average(bins, midpoints):
    total_area = sum(bins)
    if total_area <= 0:
        return None
    return sum(b * m for b, m in zip(bins, midpoints)) / total_area


def get_soil_variable(variable, sigungu_full_name, crop, service_key=None):
    """sigungu_full_name(예: "충청남도 천안시")과 crop을 받아 근사 평균값을 반환한다.

    구가 있는 시(천안시 등)는 구별로 조회해 면적 합산 후 가중평균한다.
    실패/데이터없음이면 None을 반환한다(호출부에서 결측으로 처리).
    """
    service_key = service_key or os.environ.get("SOIL_EXAM_STAT_SERVICE_KEY")
    if not service_key:
        logger.error("[soil] SOIL_EXAM_STAT_SERVICE_KEY 환경변수가 설정되어 있지 않습니다.")
        return None

    candidates = get_stdg_candidates(sigungu_full_name)
    stdg_codes = candidates["children"] or ([candidates["exact"]] if candidates["exact"] else [])
    if not stdg_codes:
        logger.warning("[soil] '%s'의 법정동코드를 찾을 수 없어 %s 조회를 건너뜁니다.", sigungu_full_name, variable)
        return None

    category = LAND_USE_CATEGORY.get(crop, "Pfld")
    combined_bins = [0.0] * 6
    got_any = False
    for stdg_cd in stdg_codes:
        areas = _fetch_operation(variable, stdg_cd, service_key)
        if areas is None:
            continue
        got_any = True
        for i in range(6):
            combined_bins[i] += areas[category][i]

    if not got_any:
        return None

    return _weighted_average(combined_bins, _get_bin_midpoints(variable, category))


# 지목별 한글 표시명 - 대체 조회 시 사용자에게 "무엇으로 대체됐는지" 보여주는 데 쓴다.
LAND_USE_LABELS = {"Rfld": "논", "Pfld": "밭", "Fachs": "시설재배지", "Fruit": "과수원"}

# 원래 지목에 등록된 필지가 없을 때 대신 시도할 지목 순서(밭이 가장 흔해 우선).
DEFAULT_FALLBACK_ORDER = ("Pfld", "Fruit", "Fachs", "Rfld")


def get_soil_variable_with_fallback(variable, sigungu_full_name, crop, service_key=None,
                                     fallback_order=DEFAULT_FALLBACK_ORDER):
    """get_soil_variable()과 같지만, crop의 원래 지목에 등록된 필지가 아예 없으면
    (예: 오이=시설재배지인데 그 지역에 시설재배지 검정 자체가 없음) 다른 지목의
    값으로 대체한다. _fetch_operation()이 애초에 지목 4개 면적을 한 번에 다
    돌려주므로, 대체를 위해 API를 추가로 더 호출하지는 않는다.

    반환: (value, used_category) - 실패 시 (None, None). used_category가
    LAND_USE_CATEGORY[crop](원래 지목)과 다르면 대체된 값이라는 뜻이니, 호출부가
    "이 값은 참고용 대체값"이라고 표시해야 한다.
    """
    service_key = service_key or os.environ.get("SOIL_EXAM_STAT_SERVICE_KEY")
    if not service_key:
        logger.error("[soil] SOIL_EXAM_STAT_SERVICE_KEY 환경변수가 설정되어 있지 않습니다.")
        return None, None

    candidates = get_stdg_candidates(sigungu_full_name)
    stdg_codes = candidates["children"] or ([candidates["exact"]] if candidates["exact"] else [])
    if not stdg_codes:
        logger.warning("[soil] '%s'의 법정동코드를 찾을 수 없어 %s 조회를 건너뜁니다.", sigungu_full_name, variable)
        return None, None

    own_category = LAND_USE_CATEGORY.get(crop, "Pfld")
    try_order = [own_category] + [c for c in fallback_order if c != own_category]
    per_category_bins = {cat: [0.0] * 6 for cat in try_order}

    got_any = False
    for stdg_cd in stdg_codes:
        areas = _fetch_operation(variable, stdg_cd, service_key)
        if areas is None:
            continue
        got_any = True
        for cat in try_order:
            for i in range(6):
                per_category_bins[cat][i] += areas[cat][i]

    if not got_any:
        return None, None

    for cat in try_order:
        bins = per_category_bins[cat]
        if sum(bins) <= 0:
            continue
        value = _weighted_average(bins, _get_bin_midpoints(variable, cat))
        if value is not None:
            return value, cat
    return None, None


def get_soil_readings(sigungu_full_name, crop, service_key=None):
    """pH/유기물/유효인산(SoilExamStat 근사평균)+EC(getSoilExamList 실측평균)를 한 번에 조회.

    pH·유기물·유효인산은 SoilExamStat V2의 지목별 구간분포 기반 근사평균(get_soil_variable)이고,
    EC는 이 통계 API에 항목이 없어 별도로 토양검정정보(getSoilExamList) 실측 시료의 <ELCD>를
    읍면동별로 모아 평균낸다(soil_ec.get_ec). 각 값은 실패/무데이터 시 None.

    반환: {"pH": float|None, "유기물": float|None, "유효인산": float|None, "EC": float|None}
    """
    readings = {}
    for variable in ("pH", "유기물", "유효인산"):
        readings[variable] = get_soil_variable(variable, sigungu_full_name, crop, service_key=service_key)
    readings["EC"] = get_ec(sigungu_full_name, service_key=service_key)
    return readings
