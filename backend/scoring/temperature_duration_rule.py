"""
사과·배 온도 위험 판정 (NH농협손해보험 과수작물 상품구조 기준)

⚠️ 2026-07-23 수정: 이전 버전에 있던 "봄철 동상해 0℃ 이하 48시간 지속" 규칙은
   실제 원문 재검증 결과 존재하지 않는 조항으로 확인되어 완전히 제거했습니다.
   실제 공식 상품구조(연합뉴스 2026-02-05 NH농협손해보험 보도자료로 확인)는:
   - 적과 전(봄철, 개화기 포함): "자연재해·조수해·화재"라는 포괄 조항만 있고
     정량적 온도×지속시간 기준 자체가 없음(현지조사로 판정)
   - 적과 후 7개 특정재해: 태풍(강풍)·우박·집중호우·가을동상해·일소피해·화재·지진
   - 이 중 정량적 공식이 확인된 건 "일소피해(폭염 33℃ 이상 연속 2일)" 뿐이고,
     "가을동상해"도 "육안으로 판별 가능한 결빙증상이 지속적으로 남아있는 경우"라는
     정성적 기준이라 온도×지속시간 공식이 없음.

   그래서 봄철 냉해(개화기 저온)는 공식 재해보험 기준 대신, 별도 프로젝트에서
   확보해둔 개화단계별 순간온도 한계표(30분간 견디는 한계온도)로 판정한다.
   ⚠️ 이 한계온도 표 자체는 이번 대화에서 원문을 재검증한 것은 아니다.
   개화단계별 "날짜" 매핑은 2026-07-23 기준: 배(천안·광주)는 국립원예특작과학원
   API의 발아~만개 실측 평균(2020~2026)으로 완전히 교체됐고, 사과는 영주·거창만
   만개일 실측 평균(2021~2026)으로 앵커링하고 나머지 8단계는 상대오프셋 근사치다
   (이 API가 만개일만 제공, 발아일은 안 줌). 안동·문경 등 실측 없는 관측소는
   순수 근사치("_generic")로 남아있다.
"""

from datetime import timedelta
from pathlib import Path

import pandas as pd

from reference_data import TEMP_THRESHOLDS_INSURANCE

BASE_DIR = Path(__file__).resolve().parents[2]  # farm-guide/
PEAR_BLOOM_CSV_PATH = BASE_DIR / "data" / "raw" / "pear_bloom_dates.csv"
APPLE_BLOOM_CSV_PATH = BASE_DIR / "data" / "raw" / "apple_bloom_dates.csv"

# 일소(폭염) 기준은 reference_data.TEMP_THRESHOLDS_INSURANCE(공식 재해보험 기준)가
# 유일한 출처다 - 여기서 값을 복제하지 않고 그대로 가져온다. 사과·배가 같은 기준을
# 쓴다는 전제를 명시적으로 검증해서, 나중에 두 작물 기준이 갈라지는데 이 가정만
# 그대로 남아있는 사고를 막는다(그 경우 import 시점에 바로 에러).
_apple_heat = TEMP_THRESHOLDS_INSURANCE["사과"]
_pear_heat = TEMP_THRESHOLDS_INSURANCE["배"]
assert _apple_heat["heat_near"] == _pear_heat["heat_near"], (
    "사과·배 일소 기준온도가 달라졌습니다 - HEAT_THRESHOLD를 작물별로 분리해야 함"
)
assert _apple_heat["heat_duration_day"] == _pear_heat["heat_duration_day"], (
    "사과·배 일소 지속일수가 달라졌습니다 - HEAT_DURATION_DAYS를 작물별로 분리해야 함"
)

HEAT_THRESHOLD = _apple_heat["heat_near"]
HEAT_DURATION_DAYS = _apple_heat["heat_duration_day"]

# 개화단계별 순간 냉해 한계온도(℃) - "30분간 견디는 한계온도" 기준.
# 출처: 별도 프로젝트에서 농촌진흥청 농업기술길잡이로부터 확보한 자료(이번 대화 재검증 안 됨)
STAGE_FROST_THRESHOLDS = {
    "사과": {
        "은색선단기": -8.9, "녹색선단기": -8.9, "녹색기": -5.6,
        "단단한화총기": -2.8, "분홍초기": -2.8, "완전분홍기": -2.2,
        "개화초기": -2.2, "만개기": -1.7, "만개이후": -1.7,
    },
    "배": {
        "꽃봉오리_화총내": -3.5, "꽃봉오리_연분홍": -2.8, "꽃봉오리_백색": -2.2,
        "개화직전": -1.9, "만개_낙화_낙화10일후유과기": -1.7,
    },
}

# 개화단계별 달력 근사 구간(월,일).
# 사과: ✅ 2026-07-23, 국립원예특작과학원 사과생육품질정보 API 실측(2021~2026, 6개년)
#      만개일자를 영주·거창에 대해 확보. 단, 이 API는 "만개일자"만 주고 발아일자는
#      안 줘서, 배처럼 발아~만개 구간을 실측으로 나눌 수는 없었다. 대신 원래
#      근사 캘린더(아래 "_generic")의 "만개기 기준 상대 오프셋"(예: 개화초기는
#      만개보다 3일 전)을 그대로 유지한 채, 만개일 자체만 실측값으로 바꿔치기했다.
#      즉 만개기 날짜는 실측이고, 나머지 8단계는 여전히 상대오프셋 근사치다.
#      안동·문경은 이번 API에 해당 농장이 없어 "_generic"(순수 근사치)으로 남는다.
STAGE_CALENDAR_APPROXIMATION = {
    "사과": {
        "_generic": [  # 실측 없는 관측소(안동·문경 등)용 순수 근사치
            ("은색선단기", (3, 15)), ("녹색선단기", (3, 25)), ("녹색기", (4, 1)),
            ("단단한화총기", (4, 8)), ("분홍초기", (4, 12)), ("완전분홍기", (4, 16)),
            ("개화초기", (4, 19)), ("만개기", (4, 22)), ("만개이후", (4, 29)),
        ],
        "영주": [  # 만개일 실측 평균(2021~2026, 6개년) = 4/20, 나머지는 상대오프셋 근사
            ("은색선단기", (3, 13)), ("녹색선단기", (3, 23)), ("녹색기", (3, 30)),
            ("단단한화총기", (4, 6)), ("분홍초기", (4, 10)), ("완전분홍기", (4, 14)),
            ("개화초기", (4, 17)), ("만개기", (4, 20)), ("만개이후", (4, 27)),
        ],
        "거창": [  # 만개일 실측 평균(2021~2026, 6개년) = 4/21
            ("은색선단기", (3, 14)), ("녹색선단기", (3, 24)), ("녹색기", (3, 31)),
            ("단단한화총기", (4, 7)), ("분홍초기", (4, 11)), ("완전분홍기", (4, 15)),
            ("개화초기", (4, 18)), ("만개기", (4, 21)), ("만개이후", (4, 28)),
        ],
    },
    "배": {
        "천안": [
            ("꽃봉오리_화총내", (3, 20)), ("꽃봉오리_연분홍", (3, 26)),
            ("꽃봉오리_백색", (3, 31)), ("개화직전", (4, 6)),
            ("만개_낙화_낙화10일후유과기", (4, 12)),
        ],
        "광주": [  # 나주(실제 배 산지) 실측값 사용 - 광주는 나주의 ASOS 대체 관측소
            ("꽃봉오리_화총내", (3, 14)), ("꽃봉오리_연분홍", (3, 20)),
            ("꽃봉오리_백색", (3, 26)), ("개화직전", (4, 2)),
            ("만개_낙화_낙화10일후유과기", (4, 8)),
        ],
    },
}
# 마지막 단계가 끝나는(개화 위험기 종료) 근사 날짜 - 만개일 + 약 7~10일
STAGE_APPROXIMATION_END = {
    "사과": {"_generic": (5, 10), "영주": (4, 27), "거창": (4, 28)},
    "배": {"천안": (4, 22), "광주": (4, 18)},
}


# ═══════════════════════════════════════════════════════════════
# 연도별 실측 발아/만개일 앵커링 — 2026-07-23 추가
#
# 위 STAGE_CALENDAR_APPROXIMATION/STAGE_APPROXIMATION_END는 "7개년 평균" 고정
# 캘린더다. 여기서는 그 해의 실측값이 있으면 그걸 우선 쓰고, 없는 연도만
# 평균 캘린더로 폴백한다.
# - 배(천안·광주): pear_bloom_dates.csv에 연도별 실측 ecln_datetm(발아)/flblms_datetm
#   (만개)이 있다. 평균 캘린더를 만들 때와 같은 방식(발아~만개 구간 균등 4분할)으로
#   그 해의 5단계 날짜를 새로 계산한다. 광주는 나주 실측값을 대신 쓴다(기존과 동일).
# - 사과(영주·거창): 이 API는 만개일만 준다. 그 해의 실측 만개일을 앵커로 삼아,
#   기존 "_generic" 캘린더에서 역산한 "만개기 기준 상대 오프셋"(고정, 아래
#   APPLE_STAGE_OFFSET_DAYS)을 그대로 적용한다 — 평균 캘린더를 만들 때와 동일한
#   방법론이고, 앵커만 그 해의 실측값으로 바뀐다.
# ═══════════════════════════════════════════════════════════════

PEAR_STATION_FARM = {"천안": "천안", "광주": "나주"}  # 관측소 -> pear_bloom_dates.csv farm_name
APPLE_STATION_FARM = {"영주": "영주", "거창": "거창"}

PEAR_STAGE_NAMES = [
    "꽃봉오리_화총내", "꽃봉오리_연분홍", "꽃봉오리_백색", "개화직전", "만개_낙화_낙화10일후유과기",
]
PEAR_END_OFFSET_DAYS = 10  # 만개일 + 10일 (기존 평균 캘린더의 천안/광주 종료일과 동일한 간격)

# "_generic" 사과 캘린더에서 만개기(4/22) 기준으로 역산한 상대 오프셋(일).
# 영주/거창의 기존 평균 캘린더도 이 오프셋을 만개일 실측 평균에 적용해 만들어진 것이다.
APPLE_STAGE_OFFSET_DAYS = {
    "은색선단기": -38, "녹색선단기": -28, "녹색기": -21, "단단한화총기": -14,
    "분홍초기": -10, "완전분홍기": -6, "개화초기": -3, "만개기": 0, "만개이후": 7,
}
APPLE_END_OFFSET_DAYS = 7  # 만개일 + 7일 (기존 영주/거창 평균 캘린더의 종료일과 동일한 간격)

_pear_bloom_cache = None
_apple_bloom_cache = None
_calendar_cache = {}  # (crop, station, year) -> (stages, end_month_day) or None


def _load_pear_bloom_by_station_year():
    global _pear_bloom_cache
    if _pear_bloom_cache is not None:
        return _pear_bloom_cache

    cache = {}
    if PEAR_BLOOM_CSV_PATH.exists():
        df = pd.read_csv(PEAR_BLOOM_CSV_PATH, encoding="utf-8-sig")
        df["ecln_datetm"] = pd.to_datetime(df["ecln_datetm"])
        df["flblms_datetm"] = pd.to_datetime(df["flblms_datetm"])
        for station, farm in PEAR_STATION_FARM.items():
            per_year = {}
            sub = df[df["farm_name"] == farm]
            for year, group in sub.groupby("year"):
                # 같은 해에 품종(species_code)이 여러 개 있으면(예: 신고/원황) 평균을 쓴다
                # (기존 "평균 캘린더" 자체도 여러 해·품종을 평균낸 것이라 방법론이 같다).
                ecln = group["ecln_datetm"].mean().date()
                flblms = group["flblms_datetm"].mean().date()
                per_year[int(year)] = (ecln, flblms)
            cache[station] = per_year
    _pear_bloom_cache = cache
    return cache


def _load_apple_bloom_by_station_year():
    global _apple_bloom_cache
    if _apple_bloom_cache is not None:
        return _apple_bloom_cache

    cache = {}
    if APPLE_BLOOM_CSV_PATH.exists():
        df = pd.read_csv(APPLE_BLOOM_CSV_PATH, encoding="utf-8-sig")
        df["flblms_datetm"] = pd.to_datetime(df["flblms_datetm"])
        for station, farm in APPLE_STATION_FARM.items():
            per_year = {}
            sub = df[df["farm_name"] == farm]
            for year, group in sub.groupby("year"):
                flblms = group["flblms_datetm"].mean().date()
                per_year[int(year)] = flblms
            cache[station] = per_year
    _apple_bloom_cache = cache
    return cache


def _quartile_stage_calendar(stage_names, start_date, end_date):
    """start_date~end_date 구간을 균등 n등분해 각 단계의 시작일을 계산한다
    (기존 평균 캘린더를 만들 때 쓴 것과 같은 방법 - 발아~만개를 25/50/75% 지점으로 근사)."""
    n = len(stage_names) - 1
    span_days = (end_date - start_date).days
    stages = []
    for i, name in enumerate(stage_names):
        d = start_date + timedelta(days=round(span_days * i / n))
        stages.append((name, (d.month, d.day)))
    return stages


def _build_pear_calendar_for_year(station, year):
    real_by_year = _load_pear_bloom_by_station_year().get(station, {})
    real = real_by_year.get(year)
    if real is None:
        return None
    ecln, flblms = real
    stages = _quartile_stage_calendar(PEAR_STAGE_NAMES, ecln, flblms)
    end = flblms + timedelta(days=PEAR_END_OFFSET_DAYS)
    return stages, (end.month, end.day)


def _build_apple_calendar_for_year(station, year):
    real_by_year = _load_apple_bloom_by_station_year().get(station, {})
    flblms = real_by_year.get(year)
    if flblms is None:
        return None
    stages = [
        (name, ((flblms + timedelta(days=offset)).month, (flblms + timedelta(days=offset)).day))
        for name, offset in APPLE_STAGE_OFFSET_DAYS.items()
    ]
    end = flblms + timedelta(days=APPLE_END_OFFSET_DAYS)
    return stages, (end.month, end.day)


def _get_calendar(crop, station, year):
    """(crop, station, year)에 맞는 (단계 리스트, 종료일) 을 반환한다. 없으면 None.

    우선순위: 그 해의 실측 발아/만개일 앵커 캘린더 -> (없으면) 7개년 평균 고정
    캘린더 -> (그것도 없으면, 사과의 안동·문경 등) "_generic" 근사치.
    """
    cache_key = (crop, station, year)
    if cache_key in _calendar_cache:
        return _calendar_cache[cache_key]

    if crop == "배":
        result = _build_pear_calendar_for_year(station, year)
        if result is None:
            stations = STAGE_CALENDAR_APPROXIMATION["배"]
            if station in stations:
                result = (stations[station], STAGE_APPROXIMATION_END["배"][station])
            else:
                result = None  # 실측 캘린더가 없는 관측소 - 오탐 방지 원칙 유지
    else:  # 사과
        result = _build_apple_calendar_for_year(station, year)
        if result is None:
            stations = STAGE_CALENDAR_APPROXIMATION["사과"]
            key = station if station in stations else "_generic"
            result = (stations[key], STAGE_APPROXIMATION_END["사과"][key])

    _calendar_cache[cache_key] = result
    return result


def _stage_for_date(crop, dt, station=None):
    """그 날짜(연도 포함)가 어느 개화단계에 속하는지 찾는다. 범위 밖이면 None.

    우선순위는 _get_calendar 참고: 그 해 실측 앵커 캘린더 -> 평균 고정 캘린더 ->
    (사과만) "_generic" 순이다.
    배: station이 천안/광주가 아니면(또는 실측·평균 캘린더가 모두 없으면) None 반환
        (오탐 방지 - 근거 없이 임의 판정하지 않음).
    """
    calendar = _get_calendar(crop, station, dt.year)
    if calendar is None:
        return None
    stages, end_date = calendar

    key = (dt.month, dt.day)
    current_stage = None
    for stage_name, start in stages:
        if key >= start:
            current_stage = stage_name
        else:
            break
    if current_stage is None:
        return None
    if key > end_date:
        return None
    return current_stage


def _find_consecutive_runs(hourly_records, condition_func):
    """
    hourly_records: [(datetime, temp), ...] 시간 오름차순 정렬된 리스트.
    condition_func(temp) -> bool 조건을 만족하는 연속 구간(run)들을 찾아
    [{"start": dt, "end": dt, "hours": n}, ...] 형태로 반환한다.
    시간 간격이 1시간이 아니거나(결측으로 시간이 비면) 연속으로 치지 않는다.
    """
    runs = []
    current_start = None
    current_end = None

    for i, (dt, temp) in enumerate(hourly_records):
        if condition_func(temp):
            if current_start is None:
                current_start = dt
                current_end = dt
            else:
                gap = (dt - current_end).total_seconds() / 3600
                if gap <= 1.01:  # 1시간 간격이면 연속으로 간주(약간의 오차 허용)
                    current_end = dt
                else:
                    # 시간이 끊겼다(결측) -> 지금까지의 run을 마감하고 새로 시작
                    runs.append({
                        "start": current_start, "end": current_end,
                        "hours": (current_end - current_start).total_seconds() / 3600 + 1,
                    })
                    current_start = dt
                    current_end = dt
        else:
            if current_start is not None:
                runs.append({
                    "start": current_start, "end": current_end,
                    "hours": (current_end - current_start).total_seconds() / 3600 + 1,
                })
                current_start = None
                current_end = None

    if current_start is not None:
        runs.append({
            "start": current_start, "end": current_end,
            "hours": (current_end - current_start).total_seconds() / 3600 + 1,
        })

    return runs


def check_spring_bloom_frost(hourly_records, crop, station_name=None):
    """
    개화단계별 순간 냉해 판정. hourly_records: [(datetime, temp), ...] (시간 오름차순).

    각 시각의 날짜를 근사 달력 매핑으로 개화단계에 대응시키고, 그 단계의 한계온도
    이하로 떨어진 시간이 있으면(=최소 1시간 관측치가 한계 이하) 위험으로 판정한다.
    (원래 기준은 "30분간 노출"이지만 시간단위 데이터라 1시간 단위로 근사한다.)

    station_name: 배는 필수(천안/광주 실측 캘린더 중 선택). 사과는 영주/거창이면 실측
        만개일 기준 캘린더, 그 외(안동·문경 등)는 근사치("_generic")로 자동 폴백된다.
    배인데 station_name이 천안/광주가 아니면 그 관측소용 실측 캘린더가 없어 항상
    triggered=False를 반환한다(오탐 방지 - 근거 없이 임의 판정하지 않음).

    반환: {"triggered": bool, "events": [...], "worst_event": {...} or None,
           "worst_near_miss": {...} or None}
        worst_near_miss: triggered 여부와 무관하게 "역대 최소 margin"(가장 위험에
        가까웠던 순간)을 항상 반환한다. 트리거된 경우 worst_event와 동일한 시각을
        가리킨다(margin>=0). 트리거 안 됐어도 margin이 0에 가장 가까웠던(가장 위험했던)
        시각을 알 수 있어, "아깝게 넘긴" 사례를 놓치지 않고 추적할 수 있다.
    """
    thresholds = STAGE_FROST_THRESHOLDS[crop]
    events = []
    all_margins = []  # 트리거 여부와 무관하게 위험기 구간의 모든 margin을 다 모아둠

    for dt, temp in hourly_records:
        stage = _stage_for_date(crop, dt, station_name)
        if stage is None:
            continue
        threshold = thresholds[stage]
        margin = threshold - temp  # 양수=초과(위험), 음수=안전(여유)
        record = {"datetime": dt, "stage": stage, "temp": temp, "threshold": threshold, "margin": margin}
        all_margins.append(record)
        if temp <= threshold:
            events.append(record)

    worst_event = max(events, key=lambda e: e["margin"]) if events else None
    worst_near_miss = max(all_margins, key=lambda e: e["margin"]) if all_margins else None

    return {
        "triggered": len(events) > 0,
        "events": events,
        "worst_event": worst_event,
        "worst_near_miss": worst_near_miss,
        "crop": crop,
        "station_name": station_name,
        "note": (
            "배: 천안·광주 실측 캘린더(2020~2026 평균, 발아~만개 전체 실측). "
            "사과: 영주·거창은 만개일만 실측(2021~2026 평균)이고 나머지 단계는 상대오프셋 "
            "근사, 안동·문경 등은 순수 근사치(_generic)."
        ),
    }


def check_heat_duration(hourly_records):
    """
    일소(폭염) 판정. hourly_records에서 일별 최고기온을 뽑아 연속 2일 이상
    33℃ 이상인지 확인한다.

    반환: {"triggered": bool, "daily_max": {date: temp, ...}, "qualifying_periods": [...]}
    """
    daily_max = {}
    for dt, temp in hourly_records:
        d = dt.date()
        if d not in daily_max or temp > daily_max[d]:
            daily_max[d] = temp

    sorted_dates = sorted(daily_max.keys())
    qualifying_periods = []
    run_start = None
    prev_date = None

    for d in sorted_dates:
        is_hot = daily_max[d] >= HEAT_THRESHOLD
        if is_hot:
            if run_start is None:
                run_start = d
            elif prev_date is not None and (d - prev_date).days > 1:
                # 날짜가 끊김(결측일) -> 새 구간 시작
                _close_heat_run(qualifying_periods, run_start, prev_date)
                run_start = d
        else:
            if run_start is not None:
                _close_heat_run(qualifying_periods, run_start, prev_date)
                run_start = None
        prev_date = d

    if run_start is not None:
        _close_heat_run(qualifying_periods, run_start, prev_date)

    triggered_periods = [p for p in qualifying_periods if p["days"] >= HEAT_DURATION_DAYS]

    return {
        "triggered": len(triggered_periods) > 0,
        "daily_max": daily_max,
        "all_hot_periods": qualifying_periods,
        "triggered_periods": triggered_periods,
    }


def _close_heat_run(periods_list, start, end):
    periods_list.append({"start": start, "end": end, "days": (end - start).days + 1})


def check_heat_margin(hourly_records):
    """
    일소(폭염) 근접사례 추적 - 냉해의 worst_near_miss와 같은 개념을 2일 조건에 적용.

    공식 기준(연속 2일 이상 33℃+)은 "온도"와 "지속일수" 두 조건이 겹쳐야 하므로,
    모든 연속 2일 구간(rolling window)에서 "그 2일 중 더 낮은 쪽의 낮 최고기온"을
    뽑아 33℃와 비교한다 — 두 날 다 33℃를 넘어야 그 window의 margin이 양수(위험)가
    되고, 하루만 넘으면 다른 하루가 발목을 잡아 margin이 음수(안전)로 남는다.
    이렇게 하면 "하루는 34℃였지만 다음날 28℃로 뚝 떨어져 지속조건 미충족"같은
    경우도 "이틀 다 32.9℃"처럼 근소하게 놓친 경우와 구분해 연속값으로 표현 가능.

    반환: {"worst_near_miss": {"margin":.., "day1":.., "day2":.., "day1_max":.., "day2_max":..} or None}
    """
    daily_max = {}
    for dt, temp in hourly_records:
        d = dt.date()
        if d not in daily_max or temp > daily_max[d]:
            daily_max[d] = temp

    sorted_dates = sorted(daily_max.keys())
    if len(sorted_dates) < 2:
        return {"worst_near_miss": None}

    windows = []
    for i in range(len(sorted_dates) - 1):
        d1, d2 = sorted_dates[i], sorted_dates[i + 1]
        if (d2 - d1).days != 1:
            continue  # 날짜가 안 붙어있으면(결측) 연속 2일이 아니므로 제외
        weaker_day_max = min(daily_max[d1], daily_max[d2])
        margin = weaker_day_max - HEAT_THRESHOLD
        windows.append({
            "margin": margin, "day1": d1, "day2": d2,
            "day1_max": daily_max[d1], "day2_max": daily_max[d2],
        })

    if not windows:
        return {"worst_near_miss": None}

    worst = max(windows, key=lambda w: w["margin"])
    return {"worst_near_miss": worst}


def check_apple_pear_temperature_risk(hourly_records, crop, station_name=None):
    """
    사과·배 온도 위험 종합 판정 (개화기 냉해 + 일소 동시 확인).
    score_temperature()의 근사치 계산 대신 이 함수의 결과를 우선 사용해야 한다.

    crop: "사과" 또는 "배" (개화단계 한계온도표가 작물별로 다르므로 필수)
    """
    frost = check_spring_bloom_frost(hourly_records, crop, station_name)
    heat = check_heat_duration(hourly_records)

    if frost["triggered"]:
        risk = "높음"
        worst = frost["worst_event"]
        reason = (
            f"개화기 냉해 위험 — {worst['stage']} 단계 한계온도({worst['threshold']}℃) "
            f"이하 관측({worst['temp']}℃, {worst['datetime']})"
        )
    elif heat["triggered"]:
        risk = "높음"
        reason = f"일소 공식기준 충족 ({HEAT_DURATION_DAYS}일 이상 {HEAT_THRESHOLD}℃ 이상)"
    else:
        risk = "낮음"
        reason = "위험 기준 미충족"

    return {"risk": risk, "reason": reason, "frost_detail": frost, "heat_detail": heat}


# ═══════════════════════════════════════════════════════════════
# scoring_engine.py 연결용 — near-miss margin 기반 0~100 연속 점수
# ═══════════════════════════════════════════════════════════════

FROST_MARGIN_SAFE_BUFFER = 2.0  # margin이 이보다 작으면(더 안전하면) 100점, 크면(더 위험하면) 0점


def _margin_to_score(margin, buffer=FROST_MARGIN_SAFE_BUFFER):
    """
    margin = threshold - temp (양수=한계 초과/위험, 음수=한계 밑돌아 안전).
    margin=-buffer 이하 -> 100점(안전). margin=+buffer 이상 -> 0점(위험).
    margin=0(정확히 한계온도) -> 50점(공식 기준선 자체이므로 중간점).
    근접사례(margin이 0에 가까운 음수)일수록 100에서 서서히 깎여, "아깝게 넘긴" 것과
    "여유있게 안전한" 것을 연속값으로 구분할 수 있다.
    """
    if margin <= -buffer:
        return 100.0
    if margin >= buffer:
        return 0.0
    frac = (margin + buffer) / (2 * buffer)  # 0~1, margin=-buffer일 때 0, +buffer일 때 1
    return 100.0 - frac * 100.0


def score_apple_pear_temperature(hourly_records, crop, station_name=None):
    """
    사과·배 전용 온도 스코어링(0~100). scoring_engine.score_crop()이 시간단위
    데이터를 갖고 있을 때 이 함수를 우선 호출하도록 연결한다(점 추정 근사 대신).

    - 냉해: worst_near_miss의 margin을 연속 점수로 변환(위 _margin_to_score).
      station에 실측 캘린더가 없으면(예: 배인데 station=영주) worst_near_miss가
      None이 되므로, 그 경우 냉해 쪽은 판단 보류(100점 취급 - 오탐 방지 원칙 유지).
    - 일소: check_heat_margin의 worst_near_miss(2일 조건 반영 연속값)를 같은 방식으로
      변환. 예전엔 트리거 여부만 봐서 "냉해 없는 해도 일소 하루 스파이크 때문에
      20점으로 깔리는" 문제가 있었는데, 이제 "그 2일 조합이 얼마나 33℃ 기준에
      가까웠는지"를 연속으로 반영해 하루짜리 스파이크(다음날 시원하면 margin이
      크게 음수가 됨)와 진짜 이틀 연속 폭염을 구분한다.
    - 최종 점수 = 냉해 점수와 일소 점수 중 더 낮은(위험한) 쪽 채택.

    반환: {"score": 0~100, "frost_score":.., "heat_score":.., "detail": {...}}
    """
    frost = check_spring_bloom_frost(hourly_records, crop, station_name)
    heat = check_heat_margin(hourly_records)

    if frost["worst_near_miss"] is not None:
        frost_score = _margin_to_score(frost["worst_near_miss"]["margin"])
    else:
        # 위험기 구간 데이터 자체가 없거나(station 실측 캘린더 없음) 위험기가 hourly_records
        # 범위 밖 -> 판단 근거가 없다는 뜻이므로, 위험하다고 임의 판정하지 않고 100점 처리.
        frost_score = 100.0

    if heat["worst_near_miss"] is not None:
        heat_score = _margin_to_score(heat["worst_near_miss"]["margin"])
    else:
        heat_score = 100.0  # 2일치도 안 되는 짧은 데이터 등 - 판단 보류, 안전 취급

    final_score = min(frost_score, heat_score)

    return {
        "score": round(final_score, 1),
        "frost_score": round(frost_score, 1),
        "heat_score": round(heat_score, 1),
        "detail": {"frost": frost, "heat": heat},
    }
