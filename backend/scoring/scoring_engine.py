"""
가중치·민감도 통합 스코어링 엔진
출처 근거: 작물5종_가중치표_최종.md

핵심 아이디어:
1. 변수마다 위험 방향이 다르다 (온도=양방향, 강수/일조=적을수록 위험,
   EC=높을수록 위험, pH/유기물/유효인산=범위 이탈이면 위험)
2. near값~위험값 구간은 선형 보간으로 100→30점 감점, 위험값을 넘으면
   같은 기울기로 계속 떨어져 0점까지 (급격한 절벽 없이 부드럽게)
3. 위험값이 없는 변수(pH 등)는 근거범위 경계에서 "완만한 버퍼"를 두고
   100→40점으로 낮춘 뒤 그 밖은 40점 고정 (이분법이지만 절벽은 피함)
4. 최종 점수 = Σ(변수별 점수 × 재정규화된 가중치) / 100
"""

from reference_data import (
    WEIGHT_MATRIX, VARIABLES,
    TEMP_THRESHOLDS_INSURANCE, TEMP_THRESHOLDS,
    PRECIP_THRESHOLDS, SUNSHINE_THRESHOLDS,
    PH_THRESHOLDS, EC_THRESHOLDS,
    ORGANIC_MATTER_THRESHOLDS, AVAILABLE_PHOSPHATE_THRESHOLDS,
    APPLE_PEAR_DANGER_APPROXIMATION_MARGIN,
    resolve_cultivation_type,
)

# 사과·배는 시간단위 데이터가 있으면 이쪽(정밀 판정)을 우선 사용한다.
# 파일이 없어도(예: 다른 프로젝트에 잘못 배치) scoring_engine 자체는 죽지 않게 방어.
try:
    from temperature_duration_rule import score_apple_pear_temperature
    _HOURLY_TEMPERATURE_AVAILABLE = True
except ImportError:
    _HOURLY_TEMPERATURE_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════
# 1. 범용 점수 변환 함수
# ═══════════════════════════════════════════════════════════════

def _linear_interpolate_beyond(value, near, danger, near_score=100, danger_score=30, floor=0):
    """
    near→danger 구간 100→30 선형 감점, danger를 넘으면 같은 기울기로 계속 감점(0에서 바닥).
    near/danger의 대소관계(냉해처럼 near>danger인지, 폭염처럼 near<danger인지)는 자동 처리.
    value가 near보다 '안전한 쪽'에 있으면 100점 그대로.
    """
    span = danger - near
    if span == 0:
        return near_score
    frac = (value - near) / span  # 0=near, 1=danger
    if frac <= 0:
        return near_score
    score = near_score - frac * (near_score - danger_score)
    if frac > 1:
        # danger 이후: 같은 기울기로 계속 하락
        extra = frac - 1
        score = danger_score - extra * (near_score - danger_score)
    return max(floor, min(near_score, score))


def _binary_range_score(value, min_v, max_v, buffer_ratio=0.10, in_score=100, out_score=40):
    """
    적정범위 안=100점. 범위 밖은 즉시 40점이 아니라, 범위폭의 10%만큼
    완충구간을 두고 100→40으로 서서히 낮춘 뒤 그 밖은 40점 고정(절벽 방지).
    """
    if value is None:
        return None
    if min_v <= value <= max_v:
        return in_score
    width = max_v - min_v
    buffer = width * buffer_ratio if width > 0 else 0.1
    if value < min_v:
        dist = min_v - value
    else:
        dist = value - max_v
    if buffer == 0:
        return out_score
    frac = min(dist / buffer, 1.0)
    return in_score - frac * (in_score - out_score)


# ═══════════════════════════════════════════════════════════════
# 2. 변수별 점수 함수
# ═══════════════════════════════════════════════════════════════

def score_temperature(crop, value, cultivation_type=None):
    """온도: 냉해near~폭염near 사이=100점, 그 밖은 위험값까지 선형 감점(양방향)."""
    if crop in TEMP_THRESHOLDS_INSURANCE:
        # 사과·배: 온도×지속시간 조합 규칙(방법 D). 단일 온도값만으로는
        # "지속시간" 조건을 판정할 수 없으므로, 여기서는 근사치로 처리하고
        # TODO: 실제로는 예보 시계열(연속 며칠/시간)이 필요함을 명시.
        th = TEMP_THRESHOLDS_INSURANCE[crop]
        cold_near, heat_near = th["cold_near"], th["heat_near"]
        if cold_near <= value <= heat_near:
            return 100
        if value < cold_near:
            # 0℃ 자체가 near이자 사실상 위험 기준(48시간 지속 조건 포함)이라
            # 근사로 cold_near보다 APPLE_PEAR_DANGER_APPROXIMATION_MARGIN℃ 더 낮은
            # 지점을 위험값으로 임시 설정(reference_data.py 공용 상수 - live_scoring.py의
            # risk_signal 근사와 동일한 값을 쓴다).
            danger = cold_near - APPLE_PEAR_DANGER_APPROXIMATION_MARGIN
            return _linear_interpolate_beyond(value, cold_near, danger, danger_score=20)
        else:
            danger = heat_near + APPLE_PEAR_DANGER_APPROXIMATION_MARGIN
            return _linear_interpolate_beyond(value, heat_near, danger, danger_score=20)

    ctype = resolve_cultivation_type(crop, cultivation_type)
    th = TEMP_THRESHOLDS[crop][ctype]
    cold_near, cold_danger = th["cold_near"], th["cold_danger"]
    heat_near, heat_danger = th["heat_near"], th["heat_danger"]

    if cold_near <= value <= heat_near:
        return 100
    if value < cold_near:
        return _linear_interpolate_beyond(value, cold_near, cold_danger)
    else:
        return _linear_interpolate_beyond(value, heat_near, heat_danger)


def score_precipitation(crop, value, cultivation_type=None):
    """강수: near값 이상=100점, near~위험값 사이 선형 감점(적을수록 위험)."""
    entry = PRECIP_THRESHOLDS[crop]
    if "near" in entry:  # 사과·배처럼 재배형태 구분 없는 경우
        near, danger = entry["near"], entry["danger"]
    else:
        ctype = resolve_cultivation_type(crop, cultivation_type)
        near, danger = entry[ctype]["near"], entry[ctype]["danger"]

    if value >= near:
        return 100
    return _linear_interpolate_beyond(value, near, danger)


def score_sunshine(crop, value, cultivation_type=None):
    """일조: near값 이상=100점, near~위험값 사이 선형 감점(부족할수록 위험)."""
    entry = SUNSHINE_THRESHOLDS[crop]
    if "near" in entry:
        near, danger = entry["near"], entry["danger"]
    else:
        ctype = resolve_cultivation_type(crop, cultivation_type)
        near, danger = entry[ctype]["near"], entry[ctype]["danger"]

    if value >= near:
        return 100
    return _linear_interpolate_beyond(value, near, danger)


def score_ph(crop, value):
    """pH: 적정범위 이분법(위험값 없음, 완충구간 적용)."""
    th = PH_THRESHOLDS[crop]
    return _binary_range_score(value, th["min"], th["max"])


def score_ec(crop, value):
    """EC: near값 이하=100점, 위험값 있으면 선형 감점, 없으면 near 기준 30% 버퍼로 완충 감점."""
    th = EC_THRESHOLDS[crop]
    near, danger = th["near"], th["danger"]
    if value <= near:
        return 100
    if danger is not None:
        return _linear_interpolate_beyond(value, near, danger)
    # 위험값 없음 → near를 기준으로 근사 위험값을 만들어(near의 30% 초과분) 선형 감점
    approx_danger = near * 1.30
    return _linear_interpolate_beyond(value, near, approx_danger)


def score_organic_matter(crop, value):
    th = ORGANIC_MATTER_THRESHOLDS[crop]
    if th is None:
        return None  # 근거 없음 — 스코어링에서 제외 처리해야 함
    return _binary_range_score(value, th["min"], th["max"])


def score_available_phosphate(crop, value):
    th = AVAILABLE_PHOSPHATE_THRESHOLDS[crop]
    if th is None:
        return None
    return _binary_range_score(value, th["min"], th["max"])


SCORE_FUNCTIONS = {
    "온도": score_temperature,
    "강수": score_precipitation,
    "일조": score_sunshine,
    "pH": score_ph,
    "EC": score_ec,
    "유기물": score_organic_matter,
    "유효인산": score_available_phosphate,
}


# ═══════════════════════════════════════════════════════════════
# 3. 통합 스코어링 (가중치 × 변수별 점수)
# ═══════════════════════════════════════════════════════════════

def score_crop(crop, usable_readings, adjusted_weights=None, cultivation_type=None,
               hourly_temp_records=None, station_name=None):
    """
    crop: 작물명
    usable_readings: dict, 변수명 → 측정값 (방어함수 통과 후의 값들만 들어와야 함.
                      결측/이상치로 제외된 변수는 키 자체가 없어야 함)
    adjusted_weights: dict, 방어함수가 재정규화한 가중치(합=100).
                      없으면 원본 WEIGHT_MATRIX를 그대로 씀(재정규화 없음 — 주의).
    cultivation_type: 온도/강수/일조처럼 재배형태별로 다른 값이 있는 변수에 사용.
                      오이/감자/상추는 필수(안 주면 MissingCultivationTypeError 발생).
                      사과/배는 재배형태 구분이 없어 무시됨.
    hourly_temp_records: [(datetime, temp), ...] 형태의 시간단위 기온 시계열.
                      사과·배에서 이게 주어지면, usable_readings["온도"]의 순간값 근사
                      대신 temperature_duration_rule의 정밀 판정(개화기 냉해 근접사례
                      margin + 일소 지속판정)을 사용해 온도 점수를 대체한다.
                      오이/감자/상추에는 적용 안 됨(해당 없음, 무시됨).
    station_name: 위 hourly_temp_records 사용 시 관측소명(냉해 캘린더 선택용, 예: "영주").

    반환: {
        "total_score": 0~100,
        "breakdown": {변수: {"value":.., "score":.., "weight":.., "contribution":..}},
        "excluded_no_reference": [...],   # 근거 자체가 없어(예: 사과 유기물) 제외된 변수
        "temperature_source": "hourly_precise" | "point_estimate"  # 온도 점수를 뭘로 냈는지
    }
    """
    if crop not in WEIGHT_MATRIX:
        raise ValueError(f"알 수 없는 작물명: {crop} (사과/배/오이/감자/상추 중 하나여야 함)")

    weights = adjusted_weights if adjusted_weights is not None else WEIGHT_MATRIX[crop]

    breakdown = {}
    excluded_no_reference = []
    weighted_sum = 0.0
    weight_used_total = 0.0
    temperature_source = "point_estimate"

    use_hourly_temp = (
        hourly_temp_records is not None
        and crop in ("사과", "배")
        and _HOURLY_TEMPERATURE_AVAILABLE
    )

    for var in VARIABLES:
        if var not in usable_readings and not (var == "온도" and use_hourly_temp):
            continue  # 방어함수가 이미 제외한 변수 (결측/이상치)

        if var == "온도" and use_hourly_temp:
            result = score_apple_pear_temperature(hourly_temp_records, crop, station_name)
            score = result["score"]
            value = f"(시간단위 {len(hourly_temp_records)}건, 상세: {result['detail']['frost']['note'][:20]}...)"
            temperature_source = "hourly_precise"
        else:
            value = usable_readings[var]
            func = SCORE_FUNCTIONS[var]
            if var in ("온도", "강수", "일조"):
                score = func(crop, value, cultivation_type)
            else:
                score = func(crop, value)

        if score is None:
            # 근거 자체가 없는 경우 (예: 사과 유기물/유효인산)
            excluded_no_reference.append(var)
            continue

        w = weights.get(var, 0)
        contribution = score * w / 100
        breakdown[var] = {"value": value, "score": round(score, 1), "weight": w,
                           "contribution": round(contribution, 2)}
        weighted_sum += contribution
        weight_used_total += w

    # 근거 없어서 제외된 변수만큼 나머지 가중치로 재정규화
    if weight_used_total > 0 and weight_used_total < 100:
        rescale = 100 / weight_used_total
        weighted_sum *= rescale
        for var in breakdown:
            breakdown[var]["contribution"] = round(breakdown[var]["contribution"] * rescale, 2)

    total_score = round(min(100, max(0, weighted_sum)), 1)

    return {
        "crop": crop,
        "total_score": total_score,
        "breakdown": breakdown,
        "excluded_no_reference": excluded_no_reference,
        "temperature_source": temperature_source,
    }
