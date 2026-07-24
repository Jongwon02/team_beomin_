"""5개 작물(사과·배·오이·감자·상추) 환경 적합도 스코어링 엔진의 결측치·이상치 방어 함수.

처리 순서: 결측 판정 -> 물리적 유효범위 검사 -> 가중치 재정규화 -> 통계적 이상치 플래그
-> 신뢰불가 판정.

⚠️ near/위험값·가중치 상수는 전부 reference_data.py에서 가져온다(직접 복제하지 않음).
   scoring_engine.py도 같은 reference_data.py를 쓰기 때문에, 방어함수와 스코어링이
   서로 다른 기준(예: 오이의 대표 재배형태가 서로 다름)으로 판단하는 일이 구조적으로
   불가능하다. cultivation_type은 재배형태 구분이 있는 작물(오이·감자·상추)에서
   필수 파라미터이며, 빠뜨리면 reference_data.MissingCultivationTypeError가 발생한다.
"""

import math
from pathlib import Path

import pandas as pd

from reference_data import (
    VARIABLES,
    LAND_USE_CATEGORY,
    get_valid_range,
    TEMP_THRESHOLDS,
    PRECIP_THRESHOLDS,
    SUNSHINE_THRESHOLDS,
    PH_PHYSICAL_RANGE,
    EC_THRESHOLDS,
    resolve_cultivation_type,
)

BASE_DIR = Path(__file__).resolve().parents[2]  # farm-guide/
WEIGHT_MATRIX_PATH = BASE_DIR / "data" / "processed" / "final_weight_matrix.csv"

CROPS = ["사과", "배", "오이", "감자", "상추"]

# 온도(냉해/폭염)는 사과·배가 "온도 x 지속시간" 조합 규칙(냉해 0℃/48h 지속, 폭염 33℃/2일
# 지속)이라 이 방어함수의 near/위험값 기반 통계적 이상치 판정 대상이 아니다(별도 시계열
# 모듈 필요). 물리적 유효범위 검사는 사과·배에도 그대로 적용된다.
CROPS_WITHOUT_TEMP_STAT_CHECK = {"사과", "배"}

DANGER_DEVIATION_RATIO = 1.2  # 위험값보다 20% 이상 더 벗어나면 플래그
NEAR_ONLY_DEVIATION_RATIO = 1.5  # 위험값 없는 변수는 near값의 1.5배 이상 벗어나면 플래그

# 결측 판정은 "개수"가 아니라 "남은 변수의 가중치 합(coverage)"으로 한다 - 흙토람
# 장애로 pH·유기물·유효인산·EC 4개가 통째로 빠져도 온도·강수·일조가 살아있으면
# 가중치 65~80%가 남아 여전히 쓸 만한 반면, 강수·일조까지 같이 빠지면 개수는 비슷해도
# 남는 가중치가 30%대로 뚝 떨어져 위험하다 - 개수 기준으로는 이 둘을 구분할 수 없다.
MIN_RELIABLE_WEIGHT_COVERAGE = 0.5  # 이 미만이면 신뢰불가(점수 계산 자체를 안 함)

# "정상" 경계는 고정 %가 아니라 작물별로 자동 도출한다(_normal_reliability_threshold).
# ⚠️ 2026-07-24 실측 검증: 고정 0.8은 EC 가중치가 크게 다른 작물(오이22% vs 배8%)
# 사이에서 역전을 만들었다 - "오이는 EC 하나만 빠져도(6/7 변수 확보) 78%로 주의"인데
# "배는 흙토람 4개(pH·유기물·유효인산·EC)가 통째로 빠져도(3/7 변수만 확보) 80%로
# 정상"이 되는 식. 흙토람이 줄 수 있는 항목은 구조적으로 이 4개뿐이므로, "이 중 EC
# 하나만 빠진 상태"를 그 작물의 "정상으로 봐줄 수 있는 최소선"으로 삼으면 작물마다
# 절대 %는 달라도 "같은 심각도"를 가리키게 되고, 4개 전부 빠지는 경우(더 심각)는
# 항상 이 경계보다 낮아 여전히 "주의"로 남는다(아래 함수 docstring 참고).


class UnknownCropError(ValueError):
    """지원하지 않는(오타 포함) 작물명이 들어왔을 때 발생."""


def _is_missing(raw_value):
    if raw_value is None:
        return True
    if isinstance(raw_value, str) and raw_value.strip() == "":
        return True
    if isinstance(raw_value, float) and math.isnan(raw_value):
        return True
    return False


def _to_float(raw_value):
    """숫자로 변환 가능하면 float, 아니면 None(=신뢰 불가한 값)을 반환한다."""
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    if math.isnan(value):
        return None
    return value


def _flag_two_sided(var, value, cold_near, cold_danger, heat_near, heat_danger):
    if cold_near <= value <= heat_near:
        return None
    if value < cold_near:
        gap = cold_near - cold_danger
        deviation = cold_near - value
    else:
        gap = heat_danger - heat_near
        deviation = value - heat_near
    if gap <= 0:
        return None
    if deviation >= DANGER_DEVIATION_RATIO * gap:
        return {"변수": var, "값": value, "사유": "위험값 대비 20% 이상 초과 이탈"}
    return None


def _flag_one_sided_low(var, value, near, danger):
    """낮을수록 나쁜 변수(강수·일조 가뭄/일조부족)."""
    if value >= near:
        return None
    if danger is None:
        threshold = near / NEAR_ONLY_DEVIATION_RATIO
        if value <= threshold:
            return {"변수": var, "값": value, "사유": "near값의 1.5배 이상 벗어난 이상치"}
        return None
    gap = near - danger
    if gap <= 0:
        return None
    deviation = near - value
    if deviation >= DANGER_DEVIATION_RATIO * gap:
        return {"변수": var, "값": value, "사유": "위험값 대비 20% 이상 초과 이탈"}
    return None


def _flag_one_sided_high(var, value, near, danger):
    """높을수록 나쁜 변수(EC)."""
    if value <= near:
        return None
    if danger is None:
        threshold = near * NEAR_ONLY_DEVIATION_RATIO
        if value >= threshold:
            return {"변수": var, "값": value, "사유": "near값의 1.5배 이상 벗어난 이상치"}
        return None
    gap = danger - near
    if gap <= 0:
        return None
    deviation = value - near
    if deviation >= DANGER_DEVIATION_RATIO * gap:
        return {"변수": var, "값": value, "사유": "위험값 대비 20% 이상 초과 이탈"}
    return None


def _flag_range_only(var, value, min_v, max_v):
    """위험값이 아예 없는 변수(pH) - 범위 안/밖 이분법만 가능."""
    if min_v <= value <= max_v:
        return None
    return {"변수": var, "값": value, "사유": "near범위 이탈(위험값 없음, 이분법 판정)"}


def _check_statistical_outlier(crop, var, value, cultivation_type):
    if var == "온도":
        if crop in CROPS_WITHOUT_TEMP_STAT_CHECK:
            return None  # 사과·배: 지속시간 조합 규칙이라 별도 모듈 필요
        ctype = resolve_cultivation_type(crop, cultivation_type)
        th = TEMP_THRESHOLDS[crop][ctype]
        return _flag_two_sided(
            var, value, th["cold_near"], th["cold_danger"], th["heat_near"], th["heat_danger"]
        )

    if var == "강수":
        entry = PRECIP_THRESHOLDS[crop]
        if "near" in entry:
            near, danger = entry["near"], entry["danger"]
        else:
            ctype = resolve_cultivation_type(crop, cultivation_type)
            near, danger = entry[ctype]["near"], entry[ctype]["danger"]
        return _flag_one_sided_low(var, value, near, danger)

    if var == "일조":
        entry = SUNSHINE_THRESHOLDS[crop]
        if "near" in entry:
            near, danger = entry["near"], entry["danger"]
        else:
            ctype = resolve_cultivation_type(crop, cultivation_type)
            near, danger = entry[ctype]["near"], entry[ctype]["danger"]
        return _flag_one_sided_low(var, value, near, danger)

    if var == "pH":
        # 작물별 "적정범위"(PH_THRESHOLDS)가 아니라 물리적 상식범위로 판정한다 -
        # 적정범위는 폭이 0.5로 좁아서 이상치 탐지에 재사용하면 정상적인 토양
        # pH조차 "최적이 아니다"는 이유로 과다플래그된다(reference_data.PH_PHYSICAL_RANGE
        # 주석 참고, 2026-07-24 진단·분리).
        lo, hi = PH_PHYSICAL_RANGE
        return _flag_range_only(var, value, lo, hi)

    if var == "EC":
        th = EC_THRESHOLDS[crop]
        return _flag_one_sided_high(var, value, th["near"], th["danger"])

    return None  # 유기물, 유효인산: near/위험값 근거 없음


def _load_weight_matrix(path=WEIGHT_MATRIX_PATH):
    return pd.read_csv(path, index_col=0, encoding="utf-8-sig")


def _crop_weights(crop, weight_matrix_df):
    row = weight_matrix_df.loc[crop]
    return {var: float(row[var]) for var in VARIABLES}


def _normal_reliability_threshold(weights):
    """작물별 "정상" 경계값을 가중치 테이블에서 자동 도출한다(수동 유지보수 불필요 -
    final_weight_matrix.csv가 바뀌면 이 값도 자동으로 같이 바뀐다).

    원칙: 흙토람 4항목(pH·유기물·유효인산·EC) 중 EC 하나만 결측인 상태의 weight_coverage를
    그 작물의 "정상" 경계로 삼는다 - "EC 하나만 빠진 건 정상으로 봐줄만하다"는 기준을
    모든 작물에 동일하게 적용하는 셈이다. pH·유기물·유효인산·EC는 전부 양(+)의
    가중치를 가지므로, 이보다 하나라도 더 빠지면(예: 흙토람 4개 전부 결측) coverage는
    이 경계보다 반드시 낮아져 "주의"(또는 그보다 더 낮으면 "신뢰불가")로 남는다 -
    "4개 전부 결측" 케이스가 실수로 통과되는 일은 구조적으로 불가능하다.
    """
    return (100 - weights["EC"]) / 100


def _renormalize_weights(weights, excluded_vars):
    """반환: (재정규화된 가중치 dict, weight_coverage). weight_coverage는 원본
    가중치(합=100) 중 제외되지 않고 남은 비율(0~1) - 신뢰도 판단에 재사용된다."""
    remaining = {v: w for v, w in weights.items() if v not in excluded_vars}
    total = sum(remaining.values())
    weight_coverage = total / 100
    if total <= 0:
        return {}, 0.0
    adjusted = {v: round(w / total * 100, 2) for v, w in remaining.items()}
    # 반올림 오차 보정: 합이 정확히 100이 되도록 잔차를 마지막 변수에 더한다.
    residual = round(100 - sum(adjusted.values()), 2)
    if residual != 0:
        last_var = list(adjusted.keys())[-1]
        adjusted[last_var] = round(adjusted[last_var] + residual, 2)
    return adjusted, weight_coverage


def _reliability_reason(excluded, flagged, weight_coverage=None):
    parts = []
    if weight_coverage is not None:
        parts.append(f"남은 가중치 비율: {weight_coverage * 100:.1f}%")
    if excluded:
        detail = ", ".join(f"{e['변수']}({e['사유']})" for e in excluded)
        parts.append(f"제외된 변수: {detail}")
    if flagged:
        detail = ", ".join(f["변수"] for f in flagged)
        parts.append(f"통계적 이상치 플래그: {detail}")
    return " / ".join(parts) if parts else ""


def guard_readings(crop, readings, cultivation_type=None, weight_matrix_df=None):
    """crop/readings를 검증해 스코어링 엔진에 바로 넣을 수 있는 형태로 정리한다.

    cultivation_type: 오이/감자/상추는 필수(reference_data.CULTIVATION_TYPES 참고).
                       빠뜨리면 MissingCultivationTypeError, 잘못된 값이면
                       InvalidCultivationTypeError가 발생한다. 사과·배는 무시된다.
                       score_crop() 호출 시에도 반드시 같은 값을 넘겨야
                       방어함수와 스코어링이 같은 재배형태 기준을 쓴다.

    물리적 유효범위(get_valid_range)는 유효인산만 crop이 대표하는 흙토람 지목
    (LAND_USE_CATEGORY)에 따라 상한이 다르다(시설재배 오이는 2500, 나머지는 2000
    그대로 - reference_data.VALID_RANGES 주석 참고). pH의 통계적 이상치 판정은
    작물별 적정범위가 아니라 물리적 상식범위(PH_PHYSICAL_RANGE=4.0~9.0)를 쓴다 -
    작물별 적정범위(PH_THRESHOLDS)는 scoring_engine의 점수 계산 전용이다.

    반환: {"usable_readings", "adjusted_weights", "excluded_variables",
           "flagged_outliers", "reliability", "reliability_reason"}
    reliability는 결측 "개수"가 아니라 남은 변수의 가중치 합(weight_coverage)
    기준 3단계다: coverage < MIN_RELIABLE_WEIGHT_COVERAGE(0.5)면 "신뢰불가"
    (adjusted_weights={}, 점수 계산 자체를 안 함), 그 이상이면서
    coverage < 그 작물의 정상 경계(_normal_reliability_threshold, 작물별로
    다름 - EC 가중치가 큰 작물일수록 경계도 낮아진다)이거나 통계적 이상치가
    있으면 "주의", 둘 다 아니면 "정상".
    """
    if crop not in CROPS:
        raise UnknownCropError(f"지원하지 않는 작물명입니다: '{crop}' (지원 작물: {', '.join(CROPS)})")

    # cultivation_type 유효성은 여기서 한 번 검증해둔다(오이/감자/상추면 필수).
    # 사과·배는 resolve_cultivation_type이 None을 그대로 반환하므로 안전하다.
    resolve_cultivation_type(crop, cultivation_type)

    weight_matrix_df = weight_matrix_df if weight_matrix_df is not None else _load_weight_matrix()
    weights = _crop_weights(crop, weight_matrix_df)
    land_use_category = LAND_USE_CATEGORY[crop]

    usable = {}
    excluded = []
    flagged = []

    for var in VARIABLES:
        raw_value = readings.get(var)
        if _is_missing(raw_value):
            excluded.append({"변수": var, "사유": "결측"})
            continue

        value = _to_float(raw_value)
        if value is None:
            excluded.append({"변수": var, "사유": "결측"})
            continue

        lo, hi = get_valid_range(var, land_use_category)
        if not (lo <= value <= hi):
            excluded.append({"변수": var, "사유": f"물리적 이상치(유효범위 {lo}~{hi} 벗어남)"})
            continue

        usable[var] = value
        flag = _check_statistical_outlier(crop, var, value, cultivation_type)
        if flag is not None:
            flagged.append(flag)

    excluded_vars = {e["변수"] for e in excluded}
    adjusted_weights, weight_coverage = _renormalize_weights(weights, excluded_vars)

    if weight_coverage < MIN_RELIABLE_WEIGHT_COVERAGE:
        return {
            "usable_readings": usable,
            "adjusted_weights": {},
            "excluded_variables": excluded,
            "flagged_outliers": flagged,
            "reliability": "신뢰불가",
            "reliability_reason": (
                f"남은 변수의 가중치 합이 {weight_coverage * 100:.1f}%로 최소 기준"
                f"({MIN_RELIABLE_WEIGHT_COVERAGE * 100:.0f}%) 미만이라 점수 계산을 할 수 없습니다. "
                + _reliability_reason(excluded, flagged)
            ),
        }

    normal_threshold = _normal_reliability_threshold(weights)
    if weight_coverage < normal_threshold or flagged:
        reliability = "주의"
    else:
        reliability = "정상"

    return {
        "usable_readings": usable,
        "adjusted_weights": adjusted_weights,
        "excluded_variables": excluded,
        "flagged_outliers": flagged,
        "reliability": reliability,
        "reliability_reason": _reliability_reason(excluded, flagged, weight_coverage),
    }
