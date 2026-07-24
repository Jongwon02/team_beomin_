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
    VALID_RANGES,
    TEMP_THRESHOLDS,
    PRECIP_THRESHOLDS,
    SUNSHINE_THRESHOLDS,
    PH_THRESHOLDS,
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

MAX_EXCLUDED_FOR_RELIABLE_SCORE = 3  # 이 값을 초과(4개 이상)하면 신뢰불가


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
        th = PH_THRESHOLDS[crop]
        return _flag_range_only(var, value, th["min"], th["max"])

    if var == "EC":
        th = EC_THRESHOLDS[crop]
        return _flag_one_sided_high(var, value, th["near"], th["danger"])

    return None  # 유기물, 유효인산: near/위험값 근거 없음


def _load_weight_matrix(path=WEIGHT_MATRIX_PATH):
    return pd.read_csv(path, index_col=0, encoding="utf-8-sig")


def _crop_weights(crop, weight_matrix_df):
    row = weight_matrix_df.loc[crop]
    return {var: float(row[var]) for var in VARIABLES}


def _renormalize_weights(weights, excluded_vars):
    remaining = {v: w for v, w in weights.items() if v not in excluded_vars}
    total = sum(remaining.values())
    if total <= 0:
        return {}
    adjusted = {v: round(w / total * 100, 2) for v, w in remaining.items()}
    # 반올림 오차 보정: 합이 정확히 100이 되도록 잔차를 마지막 변수에 더한다.
    residual = round(100 - sum(adjusted.values()), 2)
    if residual != 0:
        last_var = list(adjusted.keys())[-1]
        adjusted[last_var] = round(adjusted[last_var] + residual, 2)
    return adjusted


def _reliability_reason(excluded, flagged):
    parts = []
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

    반환: {"usable_readings", "adjusted_weights", "excluded_variables",
           "flagged_outliers", "reliability", "reliability_reason"}
    """
    if crop not in CROPS:
        raise UnknownCropError(f"지원하지 않는 작물명입니다: '{crop}' (지원 작물: {', '.join(CROPS)})")

    # cultivation_type 유효성은 여기서 한 번 검증해둔다(오이/감자/상추면 필수).
    # 사과·배는 resolve_cultivation_type이 None을 그대로 반환하므로 안전하다.
    resolve_cultivation_type(crop, cultivation_type)

    weight_matrix_df = weight_matrix_df if weight_matrix_df is not None else _load_weight_matrix()
    weights = _crop_weights(crop, weight_matrix_df)

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

        lo, hi = VALID_RANGES[var]
        if not (lo <= value <= hi):
            excluded.append({"변수": var, "사유": f"물리적 이상치(유효범위 {lo}~{hi} 벗어남)"})
            continue

        usable[var] = value
        flag = _check_statistical_outlier(crop, var, value, cultivation_type)
        if flag is not None:
            flagged.append(flag)

    if len(excluded) >= MAX_EXCLUDED_FOR_RELIABLE_SCORE + 1:
        return {
            "usable_readings": usable,
            "adjusted_weights": {},
            "excluded_variables": excluded,
            "flagged_outliers": flagged,
            "reliability": "신뢰불가",
            "reliability_reason": (
                f"7개 변수 중 {len(excluded)}개가 결측/이상치로 제외되어 점수 계산을 할 수 없습니다."
            ),
        }

    excluded_vars = {e["변수"] for e in excluded}
    adjusted_weights = _renormalize_weights(weights, excluded_vars)
    reliability = "정상" if not excluded and not flagged else "주의"

    return {
        "usable_readings": usable,
        "adjusted_weights": adjusted_weights,
        "excluded_variables": excluded,
        "flagged_outliers": flagged,
        "reliability": reliability,
        "reliability_reason": _reliability_reason(excluded, flagged),
    }
