"""기상청 지상(종관, ASOS) 일자료 조회서비스(공공데이터포털 15059093) 연동 모듈.

https://www.data.go.kr/data/15059093/openapi.do
엔드포인트: https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList

강수(sumRn, mm)·일조(sumSsHr, hr) "생육기 시작일~오늘까지 누적값"을 구하는 데 쓴다.
단기예보(weather.py)는 미래 며칠치 시간대별 예보라 이 용도(과거~오늘 누적 관측)에는
쓸 수 없어 별도 API로 분리했다.
"""

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ENDPOINT = "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
REQUEST_TIMEOUT_SECONDS = 10
NUM_OF_ROWS = 400  # 생육기간 최장(4~10월≈214일)도 한 페이지로 충분히 커버


def get_daily_records(station_id, start_date, end_date, service_key=None):
    """station_id(지점번호), start_date~end_date(YYYYMMDD 문자열)의 일자료를 받아온다.

    반환 (실패 시 None): [{"date": "20260701", "sumRn": float, "sumSsHr": float,
                            "avgTa": float, "minTa": float, "maxTa": float}, ...]
    강수/일조가 결측(공란, "관측없음" 등)이면 해당 필드는 0.0으로 채운다(관측소
    미가동일 등 실제로 "없음"인 경우가 대부분이라 강수/일조 누적 목적엔 0 처리가 맞다).
    기온(avgTa 등) 결측은 None으로 남겨 호출부가 구분할 수 있게 한다.
    """
    service_key = service_key or os.environ.get("ASOS_DALY_SERVICE_KEY")
    if not service_key:
        logger.error("[asos] ASOS_DALY_SERVICE_KEY 환경변수가 설정되어 있지 않습니다.")
        return None

    params = {
        "serviceKey": service_key,
        "pageNo": "1",
        "numOfRows": str(NUM_OF_ROWS),
        "dataType": "JSON",
        "dataCd": "ASOS",
        "dateCd": "DAY",
        "startDt": start_date,
        "endDt": end_date,
        "stnIds": str(station_id),
    }

    try:
        resp = requests.get(ENDPOINT, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        logger.error("[asos] API 요청 타임아웃 (station_id=%s, %s~%s)", station_id, start_date, end_date)
        return None
    except requests.exceptions.RequestException as e:
        logger.error("[asos] API 요청 실패: %s", e)
        return None

    try:
        data = resp.json()
    except ValueError:
        logger.error("[asos] 응답을 JSON으로 파싱할 수 없습니다: %s", resp.text[:300])
        return None

    header = data.get("response", {}).get("header", {})
    result_code = header.get("resultCode")
    if result_code != "00":
        logger.error(
            "[asos] API 오류 응답: resultCode=%s, resultMsg=%s", result_code, header.get("resultMsg")
        )
        return None

    body = data.get("response", {}).get("body", {})
    items = body.get("items", {})
    if items == "" or items is None:
        return []
    item_list = items.get("item", [])
    if isinstance(item_list, dict):  # 결과가 1건이면 리스트가 아니라 dict로 오는 경우 대비
        item_list = [item_list]

    def _num(raw):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    records = []
    for item in item_list:
        records.append({
            "date": item.get("tm", "").replace("-", ""),
            "sumRn": _num(item.get("sumRn")) or 0.0,
            "sumSsHr": _num(item.get("sumSsHr")) or 0.0,
            "avgTa": _num(item.get("avgTa")),
            "minTa": _num(item.get("minTa")),
            "maxTa": _num(item.get("maxTa")),
        })
    return records


def get_season_to_date_totals(station_id, season_start_date, today_date, service_key=None):
    """season_start_date~today_date(둘 다 YYYYMMDD)의 강수·일조 누적합을 구한다.

    반환 (실패 시 None): {"강수": float, "일조": float, "day_count": int,
                          "start_date": str, "end_date": str}
    season_start_date > today_date(아직 생육기 시작 전)이면 누적 대상 구간이 없으므로
    호출 자체를 하지 않고 {"강수": 0.0, "일조": 0.0, "day_count": 0, ...}를 반환한다
    (호출부가 day_count==0으로 "판단 보류" 여부를 알 수 있게).
    """
    if season_start_date > today_date:
        return {
            "강수": 0.0, "일조": 0.0, "day_count": 0,
            "start_date": season_start_date, "end_date": today_date,
        }

    records = get_daily_records(station_id, season_start_date, today_date, service_key=service_key)
    if records is None:
        return None

    return {
        "강수": round(sum(r["sumRn"] for r in records), 1),
        "일조": round(sum(r["sumSsHr"] for r in records), 1),
        "day_count": len(records),
        "start_date": season_start_date,
        "end_date": today_date,
    }
