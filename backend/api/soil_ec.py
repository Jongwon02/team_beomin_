"""흙토람 토양검정정보(getSoilExamList)로 지역 EC(전기전도도, ELCD) 실측 평균을 조회.

농경지화학성 통계정보(SoilExamStat V2, soil.py)에는 EC 항목이 없어 pH/유기물/유효인산만
근사평균이 나오고 EC는 늘 결측이었다. 이 API는 필지별 실측 토양검정 레코드를 그대로 주며
각 레코드의 <ELCD> 태그가 EC(dS/m)다. 시군구 안의 읍면동들을 조회해 실측값을 모아 평균낸다.

⚠️ 실측으로 확인한 두 가지 함정:
  1. 이 API는 읍면동 단위 STDG_CD(10자리)로만 데이터를 준다. 시군구 코드(뒤 6자리 0)는
     "요청 데이터 없음"을 반환한다 - 그래서 bjd_lookup.get_dong_codes로 읍면동 코드를
     펼쳐 각각 조회한다.
  2. 페이지 크기 파라미터는 대소문자를 구분한다. 반드시 'Page_Size'/'Page_No'여야 하고
     소문자 'page_size'는 무시되어 페이지당 1건만 반환된다.

엔드포인트: http://apis.data.go.kr/1390802/SoilEnviron/SoilExam/V2/getSoilExamList
서비스키는 SoilExamStat과 동일 계정 키를 공유한다(SOIL_EXAM_LIST_SERVICE_KEY 없으면
SOIL_EXAM_STAT_SERVICE_KEY로 폴백).
"""

import concurrent.futures
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from bjd_lookup import get_dong_codes

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://apis.data.go.kr/1390802/SoilEnviron/SoilExam/V2/getSoilExamList"
REQUEST_TIMEOUT_SECONDS = 10
PAGE_SIZE = 100    # 한 코드당 조회할 최대 필지 수(대부분 동/리가 이 이하)
# ⚠️ 2026-07-24 실측: 이 API가 "429 Too Many Requests" 대신 바로 "API token quota
# exceeded"(하루 호출한도 초과)를 반환하는 걸 확인했다 - 이 서비스키는 ASOS 일자료
# API와 값을 공유해서 하루 한도가 생각보다 빨리 소진된다. 지역 하나당 최대 30번씩
# 쏘던 걸 12번으로 줄여 건당 소비량을 낮췄다(그래도 균등샘플이라 대표성은 유지됨).
MAX_QUERIES = 12   # 시군구당 조회할 말단 코드 수 상한(쿼터 절약 + 응답시간 방어)
MAX_WORKERS = 12   # 병렬 HTTP 요청 수 (MAX_QUERIES 이상 둘 필요 없음)
MAX_429_RETRIES = 2       # 429(Too Many Requests) 전용 재시도 횟수 - "quota exceeded"엔 무의미하지만
RETRY_BACKOFF_SECONDS = 1.5  # 순간적인 동시요청 충돌(진짜 rate-limit)에는 여전히 도움이 됨

# 디스크 영속 캐시 - 같은 지역을 하루에 여러 번 조회해도(같은 프로세스 재시작 포함)
# API를 다시 태우지 않는다. 성공값은 하루(EC는 날마다 안 변하는 정적 데이터라 굳이
# 자주 갱신할 이유가 없음), 실패(쿼터초과/무데이터)는 3시간만 캐시해서 쿼터가
# 리셋되면 몇 시간 안에 자연스럽게 다시 시도되게 한다.
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "ec_cache.json"
SUCCESS_TTL = timedelta(hours=24)
FAILURE_TTL = timedelta(hours=3)


def _even_sample(items, k):
    """리스트에서 앞쪽 편향 없이 k개를 균등 간격으로 뽑는다(k 이하면 그대로).

    농촌 군은 말단 코드가 수백 개(리 단위)라 코드순 앞부분만 뽑으면 특정 읍면에
    쏠린다. 균등 간격 샘플로 시군구 전역에 고루 퍼지게 한다.
    """
    n = len(items)
    if n <= k:
        return items
    step = n / k
    return [items[int(i * step)] for i in range(k)]


def _fetch_dong_ec_values(stdg_cd, service_key, base_url):
    """읍면동 코드 1개의 ELCD(EC) 실측값 리스트. 실패/무데이터면 빈 리스트.

    ⚠️ 시군구 하나당 최대 MAX_WORKERS(16)개 요청이 동시에 나가는데, data.go.kr이
    이 API에 초당 요청수 제한을 걸어놔서 그중 일부가 429(Too Many Requests)로
    거부되는 걸 실측으로 확인했다(2026-07 진단). 429는 "데이터 없음"이 아니라
    "잠깐 쉬었다 다시 물어보면 될 요청"이라, 짧게 대기 후 재시도한다 - 재시도 없이
    그냥 실패 처리하면 운 나쁘게 이 읍면동들이 전부 429를 맞았을 때 EC 평균에 낄
    표본이 그만큼 줄어든다(전부 실패하면 아래 _get_ec_cached가 None을 반환).
    """
    params = {
        "serviceKey": service_key,
        "Page_Size": str(PAGE_SIZE),
        "Page_No": "1",
        "STDG_CD": stdg_cd,
    }
    for attempt in range(MAX_429_RETRIES + 1):
        try:
            resp = requests.get(base_url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            if resp.status_code == 429:
                if attempt < MAX_429_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                logger.warning("[soil_ec] 429 재시도 소진(STDG_CD=%s)", stdg_cd)
                return []
            resp.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            logger.warning("[soil_ec] getSoilExamList 요청 실패(STDG_CD=%s): %s", stdg_cd, e)
            return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        logger.warning("[soil_ec] XML 파싱 실패(STDG_CD=%s): %s", stdg_cd, resp.text[:200])
        return []

    result_code = root.findtext("./header/Result_Code")
    if result_code not in ("200", "00"):
        return []  # "요청 데이터 없음" 등 - 해당 읍면동에 시료가 없을 뿐이므로 조용히 건너뜀

    values = []
    for elcd in root.iter("ELCD"):
        text = (elcd.text or "").strip()
        if not text:
            continue
        try:
            values.append(float(text))
        except ValueError:
            continue
    return values


_disk_cache = None  # 프로세스 안에서 한 번 읽으면 재사용, 갱신 시마다 파일에도 다시 씀


def _load_disk_cache():
    global _disk_cache
    if _disk_cache is not None:
        return _disk_cache
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                _disk_cache = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("[soil_ec] 캐시 파일을 읽지 못해 새로 시작합니다: %s", e)
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
        logger.warning("[soil_ec] 캐시 파일 저장 실패(메모리 캐시로만 계속 동작): %s", e)


def _read_cache_entry(cache_key):
    """유효(TTL 안 지남) 캐시 항목이 있으면 (True, value)를, 없으면 (False, None)을 반환한다."""
    cache = _load_disk_cache()
    entry = cache.get(cache_key)
    if entry is None:
        return False, None

    fetched_at = datetime.fromisoformat(entry["fetched_at"])
    ttl = SUCCESS_TTL if entry["value"] is not None else FAILURE_TTL
    if datetime.now() - fetched_at > ttl:
        return False, None
    return True, entry["value"]


def _write_cache_entry(cache_key, value):
    cache = _load_disk_cache()
    cache[cache_key] = {"value": value, "fetched_at": datetime.now().isoformat()}
    _save_disk_cache()


def _get_ec_cached(sigungu_full_name, service_key, base_url):
    """시군구 EC 평균 조회(디스크 영속 캐시). EC는 작물과 무관해 시군구 단위로만 캐시한다.

    ⚠️ 2026-07-24: 이 API가 "429 Too Many Requests"가 아니라 "API token quota
    exceeded"(하루 호출한도 초과)를 반환하는 걸 실측으로 확인했다 - 지역 하나 조회에
    최대 MAX_QUERIES(12)번씩 API를 쏘다 보니 하루 한도가 금방 소진된다. 그래서
    프로세스 재시작에도 살아남는 디스크 캐시로 바꿨다: 성공값은 24시간, 실패는
    3시간만 캐시해서(쿼터가 리셋되면 몇 시간 안에 자연 재시도) 같은 지역을 반복
    조회해도 쿼터를 다시 쓰지 않는다.
    """
    cache_key = sigungu_full_name
    hit, cached_value = _read_cache_entry(cache_key)
    if hit:
        return cached_value

    dong_codes = get_dong_codes(sigungu_full_name)
    if not dong_codes:
        logger.warning("[soil_ec] '%s'의 말단 법정동코드를 찾을 수 없어 EC 조회를 건너뜁니다.", sigungu_full_name)
        _write_cache_entry(cache_key, None)
        return None

    if len(dong_codes) > MAX_QUERIES:
        logger.info("[soil_ec] '%s' 말단코드 %d개 중 %d개 균등샘플 조회(쿼터 절약)",
                    sigungu_full_name, len(dong_codes), MAX_QUERIES)
        dong_codes = _even_sample(dong_codes, MAX_QUERIES)

    all_values = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(_fetch_dong_ec_values, code, service_key, base_url)
            for code in dong_codes
        ]
        for future in concurrent.futures.as_completed(futures):
            all_values.extend(future.result())

    if not all_values:
        logger.warning("[soil_ec] '%s' EC 실측값을 한 건도 얻지 못했습니다.", sigungu_full_name)
        _write_cache_entry(cache_key, None)
        return None

    average = sum(all_values) / len(all_values)
    _write_cache_entry(cache_key, average)
    return average


def get_ec(sigungu_full_name, service_key=None):
    """시군구 전체명(예: "충청북도 충주시") -> 읍면동 실측 EC 평균(dS/m). 실패/무데이터면 None."""
    if not sigungu_full_name:
        return None

    service_key = (
        service_key
        or os.environ.get("SOIL_EXAM_LIST_SERVICE_KEY")
        or os.environ.get("SOIL_EXAM_STAT_SERVICE_KEY")
    )
    if not service_key:
        logger.error(
            "[soil_ec] 서비스키가 없습니다. SOIL_EXAM_LIST_SERVICE_KEY 또는 "
            "SOIL_EXAM_STAT_SERVICE_KEY 환경변수를 설정하세요."
        )
        return None

    base_url = os.environ.get("SOIL_EXAM_LIST_BASE_URL", DEFAULT_BASE_URL)
    return _get_ec_cached(sigungu_full_name, service_key, base_url)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    region = " ".join(sys.argv[1:]) or "충청북도 충주시"
    ec = get_ec(region)
    print(f"{region} EC 평균: {ec if ec is None else round(ec, 3)} dS/m")
