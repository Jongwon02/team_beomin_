"""
5개 작물 공통 - 예보 기반 "연속위험일수 카운터 + 리스크등급" 모듈.

과제 요구사항 원문: "향후 5일 예보 중 3일 이상이 작물별 위험온도 이하: 리스크등급 = 높음.
오이는 냉해 취약 작물이라 사전 보온 조치가 필요합니다." (A씨 실패의 핵심 실패 원인 대응)

지금까지 이 "연속 N일 위험구간 진입" 로직은 temperature_duration_rule.py(사과·배 폭염
2일 조건)에만 있었고, 오이·감자·상추는 현재값 하나를 near/위험값과 비교하는 점 추정
방식뿐이었다. 이 모듈은 그 구조를 5개 작물·온도/강수/일조 전 변수에 공통 적용 가능하게
일반화한다.

핵심 차이점(temperature_duration_rule.py와의 관계):
- temperature_duration_rule.py의 check_heat_margin/check_spring_bloom_frost는
  "생육단계별 정밀 판정"(개화캘린더, 30분 노출 등 도메인 특화 규칙)이라 사과·배 전용으로
  남겨둔다 - 이 모듈로 대체하지 않는다.
- 이 모듈은 "근거값(near/위험값)이 있는 모든 변수"에 대해 예보 시계열을 보고
  "몇 일 연속 위험구간인지"를 세는 범용 로직이다. 오이·감자·상추(및 사과·배 보조지표로도
  사용 가능)에 적용한다.
"""

from datetime import date


# 과제 요구사항 원문 그대로: "향후 5일 예보 중 3일 이상 위험온도 이하 -> 리스크등급 높음"
FORECAST_WINDOW_DAYS = 5
RISK_DAYS_THRESHOLD_HIGH = 3   # 5일 중 3일 이상 위험 -> 높음
RISK_DAYS_THRESHOLD_MEDIUM = 1  # 5일 중 1~2일 위험 -> 주의


def _daily_extreme(hourly_records, direction):
    """hourly_records([(datetime, value), ...])에서 일별 최저 또는 최고값을 뽑는다.
    direction: "min"(냉해·가뭄처럼 낮을수록 위험) 또는 "max"(폭염처럼 높을수록 위험)."""
    daily = {}
    for dt, value in hourly_records:
        d = dt.date()
        if d not in daily:
            daily[d] = value
        elif direction == "min":
            daily[d] = min(daily[d], value)
        else:
            daily[d] = max(daily[d], value)
    return daily


def _is_risky(value, near, danger, direction):
    """near~위험값 구간에 들어와 있는지(=위험구간 진입) 판정.
    direction="low_is_bad"(강수·일조·냉해처럼 near보다 낮으면 위험, near >= 위험보다 큰 값)
    direction="high_is_bad"(폭염·EC처럼 near보다 높으면 위험)."""
    if direction == "low_is_bad":
        return value <= near
    else:  # high_is_bad
        return value >= near


def count_consecutive_risk_days(hourly_records, near, danger, direction, today=None):
    """
    예보 시계열을 보고 "오늘부터 향후 며칠이 위험구간에 들어오는지" 카운트하고
    리스크등급을 매긴다. 과제 요구사항의 "향후 5일 중 3일 이상 위험 -> 높음"을 그대로 구현.

    hourly_records: [(datetime, value), ...] 시간 오름차순. 기상청 단기예보처럼
        보통 최대 3일 정도만 주어지는 경우가 많은데, 그보다 짧아도 있는 만큼만 판단한다
        (5일치를 다 채우라고 강제하지 않음 - 짧은 예보 기간의 한계를 그대로 인정).
    near, danger: reference_data의 near/위험값(예: 오이 냉해 near=-6.85, 위험=-8.37).
    direction: "low_is_bad" 또는 "high_is_bad".
    today: 기준일(default: hourly_records의 첫 날짜). 이후 FORECAST_WINDOW_DAYS일만 본다.

    반환: {
        "risky_days": int,              # 위험구간에 들어온 날 수
        "total_forecast_days": int,     # 실제로 예보가 있었던 날 수(5일보다 적을 수 있음)
        "risk_grade": "높음"|"주의"|"낮음",
        "daily_extremes": {date: value, ...},
        "message": str,
    }
    """
    daily_direction = "min" if direction == "low_is_bad" else "max"
    daily_extremes = _daily_extreme(hourly_records, daily_direction)

    sorted_dates = sorted(daily_extremes.keys())
    if not sorted_dates:
        return {
            "risky_days": 0, "total_forecast_days": 0, "risk_grade": "판단불가",
            "daily_extremes": {}, "message": "예보 데이터가 없어 판단할 수 없습니다.",
        }

    start = today or sorted_dates[0]
    window_dates = [d for d in sorted_dates if start <= d < start.__class__.fromordinal(start.toordinal() + FORECAST_WINDOW_DAYS)]

    risky_days = sum(1 for d in window_dates if _is_risky(daily_extremes[d], near, danger, direction))
    total_days = len(window_dates)

    if risky_days >= RISK_DAYS_THRESHOLD_HIGH:
        grade = "높음"
    elif risky_days >= RISK_DAYS_THRESHOLD_MEDIUM:
        grade = "주의"
    else:
        grade = "낮음"

    if total_days < FORECAST_WINDOW_DAYS:
        coverage_note = f" (예보가 {total_days}일치만 있어 {FORECAST_WINDOW_DAYS}일 기준의 일부만 확인됨)"
    else:
        coverage_note = ""

    message = (
        f"향후 {total_days}일 중 {risky_days}일이 위험구간입니다{coverage_note}."
    )

    return {
        "risky_days": risky_days,
        "total_forecast_days": total_days,
        "risk_grade": grade,
        "daily_extremes": daily_extremes,
        "message": message,
    }


def _josa_neun(word):
    """단어 끝음절의 받침 유무에 따라 '은'/'는' 중 맞는 조사를 고른다."""
    if not word:
        return "는"
    last_char = word[-1]
    if "가" <= last_char <= "힣":
        has_batchim = (ord(last_char) - ord("가")) % 28 != 0
        return "은" if has_batchim else "는"
    return "는"


def build_risk_signal(crop, variable, hourly_records, near, danger, direction, today=None):
    """count_consecutive_risk_days 결과를 사용자용 메시지로 감싸서 반환한다.
    reason 필드는 LLM 리포트 생성 단계에 그대로 넘길 수 있는 자연어 초안이다.
    """
    result = count_consecutive_risk_days(hourly_records, near, danger, direction, today)

    if result["risk_grade"] == "높음":
        reason = (
            f"이번 주 {variable} 위험이 {result['risky_days']}일 예상됩니다. "
            f"{crop}{_josa_neun(crop)} 이 변수에 취약한 편이라 사전 대비가 필요합니다."
        )
    elif result["risk_grade"] == "주의":
        reason = f"{variable} 위험이 {result['risky_days']}일 정도 예상되어 주의가 필요합니다."
    elif result["risk_grade"] == "판단불가":
        reason = result["message"]
    else:
        reason = f"{variable} 관련 위험은 낮게 예상됩니다."

    return {**result, "crop": crop, "variable": variable, "reason": reason}
