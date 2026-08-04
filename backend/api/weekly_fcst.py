# -*- coding: utf-8 -*-
"""단기예보 + 중기예보를 이어 붙인 '오늘부터 일주일' 일별 예보.

왜 두 API를 합치는가
  · 단기예보(getVilageFcst)    : 발표시각 기준 오늘~+3일, 3시간 간격
  · 중기예보(MidFcstInfoService): 발표일 +4일 ~ +10일 (06시 발표 기준)
어느 한쪽만으로는 일주일이 안 되고, 겹치는 구간도 없다. 경계가 정확히 +3/+4일에서
맞물리므로 단기 -> 중기 순으로 채우면 빈 날 없이 7일이 만들어진다.

일별로 합칠 때의 변환
  · 단기예보는 3시간 간격 값이라 날짜별로 접는다.
      최저/최고기온 = 그날 TMP의 min/max (TMN/TMX는 특정 시각에만 오므로 있으면 우선)
      강수확률       = 오전(00~11시)/오후(12~23시) POP의 최댓값
      날씨           = SKY/PTY 조합을 중기예보와 같은 어휘(맑음/구름많음/흐림/비...)로 맞춤
  · 중기예보는 이미 일별 + 오전/오후라 그대로 쓴다.

이 모듈은 데이터만 만든다. 기후 대처법 제시는 아직 구현하지 않는다.
"""
import datetime
import logging
from concurrent.futures import ThreadPoolExecutor

from midfcst import get_mid_forecast, latest_tmFc  # noqa: E402
from weather import get_short_term_forecast  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 7

# SKY(하늘상태) 1맑음 3구름많음 4흐림 / PTY(강수형태) 0없음 1비 2비or눈 3눈 4소나기
SKY_LABEL = {"1": "맑음", "3": "구름많음", "4": "흐림"}
PTY_LABEL = {"1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}


def _weather_label(sky, pty):
    """단기예보의 SKY/PTY를 중기예보와 같은 표현으로 맞춘다."""
    if pty and pty != "0":
        rain = PTY_LABEL.get(pty, "비")
        base = SKY_LABEL.get(sky)
        # 중기예보 어휘를 따라간다: "흐리고 비", "구름많고 비"
        if base == "흐림":
            return "흐리고 " + rain
        if base == "구름많음":
            return "구름많고 " + rain
        return rain
    return SKY_LABEL.get(sky, None)


def _is_complete_day(day):
    """하루가 온전히 담긴 예보인지. 단기예보 마지막 날은 새벽만 걸쳐 있어 불완전하다."""
    if not day.get("amWeather") or not day.get("pmWeather"):
        return False
    lo, hi = day.get("taMin"), day.get("taMax")
    if lo is None or hi is None:
        return False
    return lo != hi        # 몇 시간 치만 있으면 최저=최고로 붙어버린다


def _fold_short_term(short):
    """3시간 간격 단기예보를 날짜별 {오전/오후 날씨·강수확률, 최저/최고기온}으로 접는다."""
    out = {}
    for date_str, slots in (short.get("forecast") or {}).items():
        temps, am_pop, pm_pop = [], [], []
        am_sky = pm_sky = None
        tmn = tmx = None
        for time_str in sorted(slots):
            v = slots[time_str]
            hour = int(time_str[:2])
            if v.get("TMP") is not None:
                try:
                    temps.append(float(v["TMP"]))
                except (TypeError, ValueError):
                    pass
            if v.get("TMN") is not None:
                try:
                    tmn = float(v["TMN"])
                except (TypeError, ValueError):
                    pass
            if v.get("TMX") is not None:
                try:
                    tmx = float(v["TMX"])
                except (TypeError, ValueError):
                    pass
            pop = None
            if v.get("POP") is not None:
                try:
                    pop = int(float(v["POP"]))
                except (TypeError, ValueError):
                    pop = None
            label = _weather_label(v.get("SKY"), v.get("PTY"))
            if hour < 12:
                if pop is not None:
                    am_pop.append(pop)
                # 오전 대표는 09시에 가장 가까운 값을 쓴다(이른 새벽보다 대표성이 있음)
                if label and (am_sky is None or abs(hour - 9) < abs(am_sky[0] - 9)):
                    am_sky = (hour, label)
            else:
                if pop is not None:
                    pm_pop.append(pop)
                if label and (pm_sky is None or abs(hour - 15) < abs(pm_sky[0] - 15)):
                    pm_sky = (hour, label)

        d = "%s-%s-%s" % (date_str[:4], date_str[4:6], date_str[6:8])
        out[d] = {
            "date": d,
            "amWeather": am_sky[1] if am_sky else None,
            "pmWeather": pm_sky[1] if pm_sky else None,
            "amRainProb": max(am_pop) if am_pop else None,
            "pmRainProb": max(pm_pop) if pm_pop else None,
            "taMin": int(tmn) if tmn is not None else (int(min(temps)) if temps else None),
            "taMax": int(tmx) if tmx is not None else (int(max(temps)) if temps else None),
            "source": "단기예보",
        }
    return out


def get_weekly_forecast(lat, lon, land_reg_id, ta_reg_id, days=DEFAULT_DAYS, service_key=None):
    """오늘부터 days일치 일별 예보. 단기예보로 앞을, 중기예보로 뒤를 채운다.

    반환: {"days":[{date, dayOffset, amWeather, pmWeather, amRainProb, pmRainProb,
                    taMin, taMax, source}], "shortTerm":{...}, "mid":{...}, "missing":[...]}
    """
    today = datetime.date.today()
    wanted = [today + datetime.timedelta(days=i) for i in range(days)]
    merged = {}

    short_meta = mid_meta = None

    def _fetch_short():
        # 단기예보는 간헐적으로 타임아웃이 난다(get_short_term_forecast는 예외 대신 None을 준다).
        # 이게 실패하면 앞 4일이 통째로 비므로, 한 번은 다시 시도한다.
        for attempt in (1, 2):
            try:
                short = get_short_term_forecast(lat, lon, service_key)
            except Exception as e:                                # noqa: BLE001
                logger.error("[weekly] 단기예보 실패(%d회차): %s", attempt, e)
                short = None
            if short:
                return short
            if attempt == 1:
                logger.warning("[weekly] 단기예보가 비어 재시도합니다 (lat=%s, lon=%s)", lat, lon)
        return None

    # 단기예보(+재시도)와 중기예보는 서로 다른 API라 관계없이 동시에 요청한다 - 순차로
    # 부르면 지역 하나 조회에 2~5초씩 걸려(각각 응답에 1~2초) 지역이 여러 개일 때 체감
    # 지연이 커지고, 타임아웃까지 겹치면(단기 재시도 최대 20초 + 중기 최대 15초) 최악의
    # 경우 안 불러와지는 것처럼 보일 정도로 길어졌다.
    with ThreadPoolExecutor(max_workers=2) as ex:
        short_future = ex.submit(_fetch_short)
        mid_future = ex.submit(get_mid_forecast, land_reg_id, ta_reg_id, service_key=service_key)

        short = short_future.result()
        if short:
            short_meta = {"baseDate": short.get("base_date"), "baseTime": short.get("base_time"),
                          "nx": short.get("nx"), "ny": short.get("ny")}
            merged.update(_fold_short_term(short))

        try:
            mid = mid_future.result()
            mid_meta = {"tmFc": mid["tmFc"], "landRegId": mid["landRegId"], "taRegId": mid["taRegId"]}
            for d in mid["days"]:
                # 단기예보가 채운 날짜는 더 정밀하므로 그대로 두되, 경계일은 예외다.
                # 단기예보의 마지막 날은 예보 구간이 새벽 몇 시간만 걸쳐 있어 오후가 비고
                # 최저=최고 같은 잘린 값이 나온다. 그런 '불완전한 날'은 중기예보로 덮는다.
                cur = merged.get(d["date"])
                if cur is None or not _is_complete_day(cur):
                    merged[d["date"]] = dict(d, source="중기예보")
        except Exception as e:                                    # noqa: BLE001
            logger.error("[weekly] 중기예보 실패: %s", e)

    out, missing = [], []
    for i, day in enumerate(wanted):
        key = day.isoformat()
        if key in merged:
            out.append(dict(merged[key], date=key, dayOffset=i))
        else:
            missing.append(key)
    return {"days": out, "missing": missing, "shortTerm": short_meta, "mid": mid_meta}
