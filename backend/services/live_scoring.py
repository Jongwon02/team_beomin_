"""지역명+작물을 받아 실시간 API 호출 -> 방어처리 -> 스코어링까지 한 번에 수행하는 파이프라인.

흐름: region_mapper(관측소 매칭) -> weather(온도, 단기예보) + asos(강수·일조, ASOS
일자료 season-to-date) + soil(pH·유기물·유효인산, 흙토람) -> reading_guard(방어처리)
-> scoring_engine(최종 점수).

각 외부 API 호출은 독립적으로 실패를 흡수한다(하나가 죽어도 나머지로 계속 진행) -
개별 실패는 그 변수만 결측(None) 처리되고 guard_readings가 나머지로 재정규화한다.
"""

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]  # farm-guide/
for _sub in ("scoring", "utils", "api"):
    _path = str(BASE_DIR / "backend" / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from region_mapper import find_nearest_station, find_nearest_station_for_crop, _load_stations  # noqa: E402
from reading_guard import guard_readings  # noqa: E402
from scoring_engine import score_crop  # noqa: E402
from forecast_risk_signal import build_risk_signal  # noqa: E402
import reference_data  # noqa: E402
from weather import get_short_term_forecast  # noqa: E402
import asos  # noqa: E402
import soil  # noqa: E402

logger = logging.getLogger(__name__)

APPLE_PEAR_CROPS = ("사과", "배")

# 생육기 누적일수가 이보다 적으면 "근거값은 전체 생육기간 누적 기준"이라 판단 보류 권고.
EARLY_SEASON_MIN_DAYS = 14


def _build_hourly_records(forecast_result):
    """weather.get_short_term_forecast() 결과 -> [(datetime, temp), ...] (시간 오름차순)."""
    from datetime import datetime as _dt

    records = []
    for fcst_date, times in forecast_result.get("forecast", {}).items():
        for fcst_time, categories in times.items():
            tmp = categories.get("TMP")
            if tmp is None:
                continue
            try:
                temp = float(tmp)
                dt = _dt.strptime(fcst_date + fcst_time, "%Y%m%d%H%M")
            except (TypeError, ValueError):
                continue
            records.append((dt, temp))
    records.sort(key=lambda r: r[0])
    return records


def _resolve_current_growth_window(periods, today):
    """생육기간 정의(reference_data.get_growth_periods) + 오늘 날짜 -> 이번 생육기 윈도우.

    반환: {"status": "in_progress"|"not_started"|"completed",
           "start_date": date|None, "end_date": date|None, "message": str|None}
    """
    year = today.year
    period_dates = sorted(
        (date(year, sm, sd), date(year, em, ed)) for (sm, sd), (em, ed) in periods
    )

    for start, end in period_dates:
        if start <= today <= end:
            return {"status": "in_progress", "start_date": start, "end_date": today, "message": None}

    first_start = period_dates[0][0]
    if today < first_start:
        window_desc = ", ".join(f"{s.strftime('%m/%d')}~{e.strftime('%m/%d')}" for s, e in period_dates)
        return {
            "status": "not_started", "start_date": None, "end_date": None,
            "message": f"생육기 시작 전(생육기: {window_desc}) - 강수/일조 누적값은 판단 보류",
        }

    completed = [p for p in period_dates if p[1] < today]
    last_start, last_end = max(completed, key=lambda p: p[1])
    return {
        "status": "completed", "start_date": last_start, "end_date": last_end,
        "message": (
            f"생육기({last_start.strftime('%m/%d')}~{last_end.strftime('%m/%d')}) 종료 후 - "
            f"해당 생육기 전체 누적값 기준"
        ),
    }


def _get_temperature_reading(station_lat, station_lon, crop, matched_station):
    """온도 관련 값을 조회한다. 실패 시 (None, None, "실패 사유")."""
    try:
        forecast = get_short_term_forecast(station_lat, station_lon)
    except Exception as e:
        logger.error("[live_scoring] 단기예보 API 호출 중 예외: %s", e)
        return None, None, f"기상청 단기예보 API 예외: {e}"

    if forecast is None:
        return None, None, "기상청 단기예보 API 호출 실패"

    hourly_records = _build_hourly_records(forecast)
    if not hourly_records:
        return None, None, "단기예보 응답에 유효한 기온(TMP) 데이터 없음"

    point_estimate = hourly_records[0][1]  # 가장 가까운 시각의 기온 - 물리적 유효성 검사/가중치용 근사값
    return point_estimate, hourly_records, None


_RISK_GRADE_ORDER = {"판단불가": -1, "낮음": 0, "주의": 1, "높음": 2}


def _combine_risk_grade(grade_a, grade_b):
    """둘 중 더 위험한(높은) 등급을 고른다."""
    return grade_a if _RISK_GRADE_ORDER.get(grade_a, -1) >= _RISK_GRADE_ORDER.get(grade_b, -1) else grade_b


def _build_temperature_risk_signals(crop, cultivation_type, hourly_records):
    """forecast_risk_signal.build_risk_signal()로 냉해·폭염 "연속위험일수" 신호를 만든다.

    사과·배: TEMP_THRESHOLDS_INSURANCE(공식 재해보험 기준)를 쓴다. 이 표에는 danger값이
        없어(cold_near/heat_near만 있음) scoring_engine.score_temperature()와 동일한
        근사(reference_data.APPLE_PEAR_DANGER_APPROXIMATION_MARGIN)로 danger를 만든다 -
        두 곳이 같은 reference_data.py 상수를 가져다 쓰므로 근사폭이 어긋날 수 없다.
        temperature_duration_rule의 정밀 판정(개화캘린더 등)을 대체하지 않는 보조 지표다.
    오이·감자·상추: TEMP_THRESHOLDS[crop][cultivation_type]의 near/위험값을 그대로 쓴다.
        이 작물들은 정밀 판정 모듈이 없어 이 신호가 사실상 유일한 "연속위험일수" 근거다.

    hourly_records가 없으면(예보 조회 실패) 빈 리스트로 넘겨 build_risk_signal이
    "판단불가" 등급으로 안전하게 처리하게 한다.
    """
    records = hourly_records or []
    today = date.today()

    if crop in APPLE_PEAR_CROPS:
        th = reference_data.TEMP_THRESHOLDS_INSURANCE[crop]
        cold_near, heat_near = th["cold_near"], th["heat_near"]
        margin = reference_data.APPLE_PEAR_DANGER_APPROXIMATION_MARGIN
        cold_danger, heat_danger = cold_near - margin, heat_near + margin
    else:
        ctype = reference_data.resolve_cultivation_type(crop, cultivation_type)
        th = reference_data.TEMP_THRESHOLDS[crop][ctype]
        cold_near, cold_danger = th["cold_near"], th["cold_danger"]
        heat_near, heat_danger = th["heat_near"], th["heat_danger"]

    frost_signal = build_risk_signal(crop, "냉해(저온)", records, cold_near, cold_danger, "low_is_bad", today=today)
    heat_signal = build_risk_signal(crop, "폭염(고온)", records, heat_near, heat_danger, "high_is_bad", today=today)

    return {
        "냉해": frost_signal,
        "폭염": heat_signal,
        "overall_risk_grade": _combine_risk_grade(frost_signal["risk_grade"], heat_signal["risk_grade"]),
    }


def _get_precip_sunshine_reading(station_id, crop, cultivation_type):
    """강수·일조 season-to-date 누적값을 조회한다.

    반환: (readings: {"강수":.., "일조":..} 각 None 가능, source_note: str, flags: [str])
    """
    flags = []
    try:
        periods = reference_data.get_growth_periods(crop, cultivation_type)
    except Exception as e:
        logger.error("[live_scoring] 생육기간 조회 실패: %s", e)
        return {"강수": None, "일조": None}, f"생육기간 정의 조회 실패: {e}", flags

    today = date.today()
    window = _resolve_current_growth_window(periods, today)

    if window["message"]:
        flags.append(window["message"])

    if window["status"] == "not_started":
        return {"강수": None, "일조": None}, "ASOS 일자료 API (생육기 시작 전 - 미조회)", flags

    # ASOS 일자료는 전날까지만 제공됨(실측 확인) - 종료일을 어제로 캡.
    asos_cutoff = today - timedelta(days=1)
    effective_end = min(window["end_date"], asos_cutoff)
    if effective_end < window["start_date"]:
        return {"강수": None, "일조": None}, "ASOS 일자료 API (전날까지만 제공되어 아직 조회 불가)", flags

    start_str = window["start_date"].strftime("%Y%m%d")
    end_str = effective_end.strftime("%Y%m%d")

    try:
        totals = asos.get_season_to_date_totals(station_id, start_str, end_str)
    except Exception as e:
        logger.error("[live_scoring] ASOS 일자료 API 호출 중 예외: %s", e)
        return {"강수": None, "일조": None}, f"ASOS 일자료 API 예외: {e}", flags

    if totals is None:
        return {"강수": None, "일조": None}, "ASOS 일자료 API 호출 실패", flags

    if 0 < totals["day_count"] < EARLY_SEASON_MIN_DAYS:
        flags.append(
            f"생육기 초반({totals['day_count']}일 경과)이라 강수/일조 누적값의 신뢰도가 낮을 수 있음 - 판단 보류 권장"
        )

    source_note = (
        f"ASOS 일자료 season-to-date ({start_str}~{end_str}, {totals['day_count']}일 누적)"
    )
    return {"강수": totals["강수"], "일조": totals["일조"]}, source_note, flags


def _get_soil_reading(sigungu_full_name, crop):
    if not sigungu_full_name:
        return {"pH": None, "유기물": None, "유효인산": None, "EC": None}, "흙토람 API (지역 법정동코드 확인 불가)"
    try:
        readings = soil.get_soil_readings(sigungu_full_name, crop)
    except Exception as e:
        logger.error("[live_scoring] 흙토람 API 호출 중 예외: %s", e)
        return {"pH": None, "유기물": None, "유효인산": None, "EC": None}, f"흙토람 API 예외: {e}"

    return readings, "흙토람 SoilExamStat V2(pH·유기물·유효인산 지목별 구간분포 근사평균) + 토양검정정보 getSoilExamList(EC 읍면동 실측평균)"


def get_live_score(region_name, crop):
    """region_name+crop을 받아 실시간 API 조회부터 최종 스코어링까지 수행한다.

    반환 예시:
    {
        "status": "matched",
        "input_region": ..., "crop": ...,
        "matched_station": ..., "cultivation_type": ..., "distance_km": ...,
        "total_score": ..., "breakdown": {...}, "excluded_no_reference": [...],
        "temperature_source": "hourly_precise"|"point_estimate",
        "reliability": "정상"|"주의"|"신뢰불가", "reliability_reason": ...,
        "excluded_variables": [...], "flagged_outliers": [...],
        "flags": [...],  # 생육기 초반 등 파이프라인 자체 경고
        "data_sources": {"온도":.., "강수":.., "일조":.., "토양":..},
        "risk_signals": {"온도": {"냉해": {...}, "폭염": {...}, "overall_risk_grade": ...}},
    }
    실패 시: {"status": "not_found"|"ambiguous", "input_region", "crop", ...}
    지원하지 않는 작물명이면 find_nearest_station_for_crop과 동일하게 ValueError.
    """
    crop_match = find_nearest_station_for_crop(region_name, crop)
    if crop_match["status"] != "matched":
        return {"status": crop_match["status"], "input_region": region_name, "crop": crop, **{
            k: v for k, v in crop_match.items() if k not in ("status", "input_region", "crop")
        }}

    matched_station = crop_match["matched_station"]
    cultivation_type = crop_match.get("cultivation_type")

    stations = _load_stations()
    station_info = next((s for s in stations if s["station_name"] == matched_station), None)
    if station_info is None:
        return {
            "status": "not_found", "input_region": region_name, "crop": crop,
            "message": f"관측소 '{matched_station}'의 좌표를 region_cluster_map.json에서 찾을 수 없습니다.",
        }

    region_match = find_nearest_station(region_name)
    sigungu_full_name = (
        region_match["matched_region"]["sigungu_name"] if region_match.get("status") == "matched" else None
    )

    flags = []
    data_sources = {}

    # 2. 온도 (기상청 단기예보)
    point_temp, hourly_records, temp_error = _get_temperature_reading(
        station_info["lat"], station_info["lon"], crop, matched_station
    )
    data_sources["온도"] = "기상청 단기예보 API" if temp_error is None else f"기상청 단기예보 API 실패({temp_error})"
    if temp_error:
        flags.append(f"온도 조회 실패: {temp_error}")

    # 2-1. 온도 연속위험일수 신호(forecast_risk_signal) - 오이·감자·상추는 필수(유일한
    # 연속위험일수 근거), 사과·배는 temperature_duration_rule 정밀판정의 보조지표.
    # ⚠️ 강수·일조는 near/위험값이 "생육기간 전체 누적값"(수백mm/수백시간) 기준이라
    # 예보의 "하루치" 값과 단위가 안 맞아(항상 위험판정으로 나옴) 여기 포함하지 않았다 -
    # 이 모듈은 온도처럼 "그날의 순간/일별 극값"이 근거값과 같은 단위인 변수에만 유효하다.
    try:
        risk_signals = {"온도": _build_temperature_risk_signals(crop, cultivation_type, hourly_records)}
    except Exception as e:
        logger.error("[live_scoring] 위험일수 신호 계산 실패: %s", e)
        risk_signals = {"온도": {"error": str(e)}}
        flags.append(f"위험일수 신호 계산 실패: {e}")

    # 3. 강수·일조 (ASOS 일자료 season-to-date)
    precip_sun_readings, ps_source, ps_flags = _get_precip_sunshine_reading(
        station_info["station_id"], crop, cultivation_type
    )
    data_sources["강수"] = ps_source
    data_sources["일조"] = ps_source
    flags.extend(ps_flags)

    # 4. 토양 (흙토람)
    soil_readings, soil_source = _get_soil_reading(sigungu_full_name, crop)
    data_sources["토양"] = soil_source

    readings = {
        "온도": point_temp,
        "강수": precip_sun_readings["강수"],
        "일조": precip_sun_readings["일조"],
        "pH": soil_readings["pH"],
        "유기물": soil_readings["유기물"],
        "유효인산": soil_readings["유효인산"],
        "EC": soil_readings["EC"],
    }

    # 5. 결측/이상치 방어
    guard_result = guard_readings(crop, readings, cultivation_type)

    # 6. 최종 스코어링 (사과/배는 시간단위 정밀 온도판정 추가 반영)
    score_kwargs = {}
    use_hourly = crop in APPLE_PEAR_CROPS and hourly_records
    if use_hourly:
        score_kwargs["hourly_temp_records"] = hourly_records
        score_kwargs["station_name"] = matched_station

    score_result = score_crop(
        crop, guard_result["usable_readings"],
        adjusted_weights=guard_result["adjusted_weights"],
        cultivation_type=cultivation_type,
        **score_kwargs,
    )

    return {
        "status": "matched",
        "input_region": region_name,
        "crop": crop,
        "matched_station": matched_station,
        "cultivation_type": cultivation_type,
        "distance_km": crop_match["distance_km"],
        "station_warning": crop_match.get("warning"),
        "total_score": score_result["total_score"],
        "breakdown": score_result["breakdown"],
        "excluded_no_reference": score_result["excluded_no_reference"],
        "temperature_source": score_result["temperature_source"],
        "reliability": guard_result["reliability"],
        "reliability_reason": guard_result["reliability_reason"],
        "excluded_variables": guard_result["excluded_variables"],
        "flagged_outliers": guard_result["flagged_outliers"],
        "flags": flags,
        "data_sources": data_sources,
        "raw_readings": readings,
        "risk_signals": risk_signals,
    }
