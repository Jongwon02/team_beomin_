# -*- coding: utf-8 -*-
"""기상청 중기예보 조회서비스(MidFcstInfoService) 호출.

가이드: 기상청28_중기예보 조회서비스_오픈API활용가이드_241128.docx
구역코드: 중기예보_중기기온예보구역코드_2025.12.xlsx

두 오퍼레이션을 쓴다. **regId 체계가 서로 다르다**는 게 가장 큰 함정이다.
  · getMidLandFcst (중기육상예보) - 날씨/강수확률. regId = 광역권 코드(끝 0000, 특성 A)
        예) 충청북도 11C10000
  · getMidTa       (중기기온)     - 최저/최고기온. regId = 도시 코드(특성 C)
        예) 충주 11C10101

예보 기간(2024-11-28 14시 이후 개정):
  · 06시 발표 -> 발표일 +4일 ~ +10일
  · 18시 발표 -> 발표일 +5일 ~ +10일
tmFc는 YYYYMMDDHHMM 형식이고 06:00/18:00 두 시각만 유효하며, 최근 24시간 자료만 제공된다.

따라서 "오늘부터 일주일"을 채우려면 단기예보(backend/api/weather.py, +0~+3일)와
이어 붙여야 한다 - 이 모듈은 중기 구간만 책임진다.
"""
import datetime
import logging
import os

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://apis.data.go.kr/1360000/MidFcstInfoService"
OP_LAND = "getMidLandFcst"
OP_TA = "getMidTa"
REQUEST_TIMEOUT_SECONDS = 15

# 중기예보가 제공하는 "발표일 + N일" 범위
MID_DAY_MIN_0600 = 4
MID_DAY_MIN_1800 = 5
MID_DAY_MAX = 10


def _service_key(explicit=None):
    return explicit or os.environ.get("KMA_SERVICE_KEY") or os.environ.get("MID_FCST_SERVICE_KEY")


def latest_tmFc(now=None):
    """지금 시각에 유효한 가장 최근 발표시각(tmFc)과 그 기준일을 돌려준다.

    발표는 06:00/18:00 두 번이고 최근 24시간 자료만 제공되므로,
    06시 전이면 '어제 18시', 18시 전이면 '오늘 06시', 그 뒤면 '오늘 18시'를 쓴다.
    발표 직후 몇 분간은 아직 자료가 안 올라와 있을 수 있어 10분 여유를 둔다.
    """
    now = now or datetime.datetime.now()
    grace = datetime.timedelta(minutes=10)
    today = now.date()
    for hh in (18, 6):
        anchor = datetime.datetime.combine(today, datetime.time(hh, 0))
        if now >= anchor + grace:
            return anchor.strftime("%Y%m%d%H%M"), anchor
    anchor = datetime.datetime.combine(today - datetime.timedelta(days=1), datetime.time(18, 0))
    return anchor.strftime("%Y%m%d%H%M"), anchor


def _call(operation, reg_id, tm_fc, service_key=None):
    key = _service_key(service_key)
    if not key:
        raise RuntimeError("KMA_SERVICE_KEY가 없습니다(.env 확인)")
    params = {
        "serviceKey": key, "numOfRows": 10, "pageNo": 1,
        "dataType": "JSON", "regId": reg_id, "tmFc": tm_fc,
    }
    url = "%s/%s" % (BASE_URL, operation)
    resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    # 키 오류·구독 미승인 등은 200으로 XML 에러 문서를 돌려주는 경우가 있다
    text = resp.text.lstrip()
    if not text.startswith("{"):
        raise RuntimeError("JSON이 아닌 응답(키/승인 문제일 수 있음): %s" % text[:200])
    body = resp.json().get("response", {})
    header = body.get("header", {})
    if header.get("resultCode") not in ("00", "0"):
        raise RuntimeError("API 오류 %s: %s" % (header.get("resultCode"), header.get("resultMsg")))
    items = ((body.get("body") or {}).get("items") or {}).get("item") or []
    if isinstance(items, dict):
        items = [items]
    if not items:
        raise RuntimeError("자료 없음 (regId=%s, tmFc=%s)" % (reg_id, tm_fc))
    return items[0]


def get_mid_land_fcst(reg_id, tm_fc=None, service_key=None):
    """중기육상예보 원본 1건 (regId는 광역권 코드)."""
    tm_fc = tm_fc or latest_tmFc()[0]
    return _call(OP_LAND, reg_id, tm_fc, service_key)


def get_mid_ta(reg_id, tm_fc=None, service_key=None):
    """중기기온 원본 1건 (regId는 도시 코드)."""
    tm_fc = tm_fc or latest_tmFc()[0]
    return _call(OP_TA, reg_id, tm_fc, service_key)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else f


def get_mid_forecast(land_reg_id, ta_reg_id, tm_fc=None, service_key=None):
    """육상예보 + 기온을 합쳐 날짜별로 펼친다.

    반환: {"tmFc":..., "baseDate":..., "landRegId":..., "taRegId":...,
           "days": [{"date","dayOffset","amWeather","pmWeather","amRainProb",
                     "pmRainProb","taMin","taMax"}, ...]}
    18시 발표에는 +4일 항목이 없으므로 그 날짜는 건너뛴다.
    """
    if tm_fc is None:
        tm_fc, base = latest_tmFc()
    else:
        base = datetime.datetime.strptime(tm_fc, "%Y%m%d%H%M")
    land = get_mid_land_fcst(land_reg_id, tm_fc, service_key)
    ta = get_mid_ta(ta_reg_id, tm_fc, service_key)

    day_min = MID_DAY_MIN_0600 if base.hour == 6 else MID_DAY_MIN_1800
    days = []
    for n in range(day_min, MID_DAY_MAX + 1):
        # 8일 이후는 오전/오후 구분이 없어 하나의 값만 온다
        if n <= 7:
            am_w, pm_w = land.get("wf%dAm" % n), land.get("wf%dPm" % n)
            am_r, pm_r = _num(land.get("rnSt%dAm" % n)), _num(land.get("rnSt%dPm" % n))
        else:
            am_w = pm_w = land.get("wf%d" % n)
            am_r = pm_r = _num(land.get("rnSt%d" % n))
        if am_w is None and pm_w is None:
            continue
        days.append({
            "date": (base.date() + datetime.timedelta(days=n)).isoformat(),
            "dayOffset": n,
            "amWeather": am_w, "pmWeather": pm_w,
            "amRainProb": am_r, "pmRainProb": pm_r,
            "taMin": _num(ta.get("taMin%d" % n)), "taMax": _num(ta.get("taMax%d" % n)),
        })
    return {
        "tmFc": tm_fc, "baseDate": base.isoformat(sep=" "),
        "landRegId": land_reg_id, "taRegId": ta_reg_id, "days": days,
    }
