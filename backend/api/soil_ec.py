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
import functools
import logging
import os
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv

from bjd_lookup import get_dong_codes

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://apis.data.go.kr/1390802/SoilEnviron/SoilExam/V2/getSoilExamList"
REQUEST_TIMEOUT_SECONDS = 10
PAGE_SIZE = 100    # 한 코드당 조회할 최대 필지 수(대부분 동/리가 이 이하)
MAX_QUERIES = 30   # 시군구당 조회할 말단 코드 수 상한(응답시간 방어)
MAX_WORKERS = 16   # 병렬 HTTP 요청 수


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
    """읍면동 코드 1개의 ELCD(EC) 실측값 리스트. 실패/무데이터면 빈 리스트."""
    params = {
        "serviceKey": service_key,
        "Page_Size": str(PAGE_SIZE),
        "Page_No": "1",
        "STDG_CD": stdg_cd,
    }
    try:
        resp = requests.get(base_url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
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


@functools.lru_cache(maxsize=256)
def _get_ec_cached(sigungu_full_name, service_key, base_url):
    """시군구 EC 평균 조회(캐시). EC는 작물과 무관해 시군구 단위로만 캐시한다."""
    dong_codes = get_dong_codes(sigungu_full_name)
    if not dong_codes:
        logger.warning("[soil_ec] '%s'의 말단 법정동코드를 찾을 수 없어 EC 조회를 건너뜁니다.", sigungu_full_name)
        return None

    if len(dong_codes) > MAX_QUERIES:
        logger.info("[soil_ec] '%s' 말단코드 %d개 중 %d개 균등샘플 조회(응답시간 방어)",
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
        return None

    return sum(all_values) / len(all_values)


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
