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

import logging
import os
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv

from bjd_lookup import get_stdg_candidates
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


# 작물별로 어느 지목(land-use) 분포를 대표값으로 쓸지. 사과·배는 과수원, 오이는
# 대부분 시설재배, 감자·상추는 노지 밭 재배가 기본형이라는 일반 농업지식 기반 매핑.
LAND_USE_CATEGORY = {
    "사과": "Fruit", "배": "Fruit",
    "오이": "Fachs",
    "감자": "Pfld", "상추": "Pfld",
}


def _fetch_operation(variable, stdg_cd, service_key):
    url = BASE_URL + OPERATIONS[variable]
    params = {"serviceKey": service_key, "pageNo": "1", "numOfRows": "10", "STDG_CD": stdg_cd}
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
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
