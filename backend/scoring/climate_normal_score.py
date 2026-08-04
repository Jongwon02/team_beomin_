"""실시간 예보 대신 여러 해 평년 통계로 낸 안정적인 작물 적합도 점수.

접속 시점(오늘 날씨·이번 시즌 진행 정도)에 따라 등급이 뒤바뀌는 문제 때문에 만들었다
(같은 지역·같은 작물인데 폭염일 때 조회하면 "위험", 평소에 조회하면 "우수"가 나오는
문제 - 사용자는 "이 작물을 이 지역에서 키우기 얼마나 쉬운지"를 알고 싶은 거지 "오늘
날씨"를 알고 싶은 게 아니다).

- 온도·강수·일조: data/scripts/compute_climate_normal.py(오이·감자·상추 - ASOS
  다년치 일자료)와 compute_fruit_temp_normal.py(사과·배 - 로컬 시간단위 다년치)가
  미리 계산해둔 "여러 해 평균 채점 결과"(data/processed/climate_normal_scores.json)를
  그대로 쓴다. ⚠️ 근값(near)이 "평균 ± 표준편차"로 만들어져 있어서, 평년값(평균) 자체를
  다시 근값과 비교하면 항상 100점만 나온다(그 평균으로 근값을 만들었으니 당연히 안전한
  쪽에 있음) - 그래서 "매년 값을 개별 채점 후 평균"까지 미리 끝내둔 결과를 쓴다. 상세
  이유는 저 스크립트들의 주석 참고.
- pH·유기물·유효인산·EC: 흙토람 자체가 이미 "누적 검정 통계"라 실시간성이 없으므로
  그대로 실시간 조회(soil.get_soil_readings)를 쓴다.
"""

import json
import logging
from pathlib import Path

from reference_data import WEIGHT_MATRIX, resolve_cultivation_type, LAND_USE_CATEGORY
from scoring_engine import score_ph, score_ec, score_organic_matter, score_available_phosphate
from region_mapper import find_nearest_station, find_nearest_station_for_crop
import soil

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]  # farm-guide/ 또는 저장소 루트
NORMAL_SCORES_PATH = BASE_DIR / "data" / "processed" / "climate_normal_scores.json"

_normal_cache = None


def _load_normal_scores():
    global _normal_cache
    if _normal_cache is not None:
        return _normal_cache
    if not NORMAL_SCORES_PATH.exists():
        logger.warning("[climate_normal_score] %s 파일이 없습니다.", NORMAL_SCORES_PATH)
        _normal_cache = {}
        return _normal_cache
    with open(NORMAL_SCORES_PATH, encoding="utf-8") as f:
        _normal_cache = json.load(f)
    return _normal_cache


def _renormalize(weights, excluded_vars):
    """reading_guard._renormalize_weights와 동일한 규칙 - 결측 변수의 가중치를
    남은 변수로 재분배한다. 반환: (재정규화된 가중치, weight_coverage 0~1)."""
    remaining = {v: w for v, w in weights.items() if v not in excluded_vars}
    total = sum(remaining.values())
    if total <= 0:
        return {}, 0.0
    adjusted = {v: round(w / total * 100, 2) for v, w in remaining.items()}
    residual = round(100 - sum(adjusted.values()), 2)
    if residual != 0 and adjusted:
        last_var = list(adjusted.keys())[-1]
        adjusted[last_var] = round(adjusted[last_var] + residual, 2)
    return adjusted, total / 100


def get_climate_normal_score(region_name, crop):
    """region_name+crop -> 평년 통계 기반 적합도.

    반환 형태 (실패 시 status가 matched가 아님):
    {
        "status": "matched", "input_region", "crop",
        "matched_station", "cultivation_type", "distance_km", "station_warning",
        "total_score": 0~100, "breakdown": {변수: {"score", "weight", "source"}},
        "excluded_variables": [...], "years_used": {...}
    }
    """
    crop_match = find_nearest_station_for_crop(region_name, crop)
    if crop_match["status"] != "matched":
        return {"status": crop_match["status"], "input_region": region_name, "crop": crop,
                **{k: v for k, v in crop_match.items() if k not in ("status", "input_region", "crop")}}

    matched_station = crop_match["matched_station"]
    cultivation_type = crop_match.get("cultivation_type")
    ctype_key = cultivation_type or ""

    normals = _load_normal_scores()
    normal_key = f"{crop}|{matched_station}|{ctype_key}"
    normal_entry = normals.get(normal_key)

    region_match = find_nearest_station(region_name)
    sigungu_full_name = (
        region_match["matched_region"]["sigungu_name"] if region_match.get("status") == "matched" else None
    )
    cluster_id = cluster_name = None
    if region_match.get("status") == "matched":
        cluster_id = region_match["station"]["cluster_id"]
        cluster_name = region_match["station"].get("cluster_name")

    weights = WEIGHT_MATRIX[crop]
    breakdown = {}
    excluded = []

    def add(var, score, source, note=None):
        if score is None:
            excluded.append(var)
        else:
            entry = {"score": round(score, 1), "weight": weights[var], "source": source}
            if note:
                entry["note"] = note
            breakdown[var] = entry

    if normal_entry:
        add("온도", normal_entry.get("온도_score"), "평년(다년 평균 채점)")
        add("강수", normal_entry.get("강수_score"), "평년(다년 평균 채점)")
        add("일조", normal_entry.get("일조_score"), "평년(다년 평균 채점)")
    else:
        excluded.extend(["온도", "강수", "일조"])

    # pH·유기물·유효인산은 작물의 지목(예: 오이=시설재배지)에 등록된 흙토람 필지가
    # 아예 없으면(예: 그 지역에 시설재배지 검정 자체가 없음) 다른 지목 값으로
    # 대체한다 - 그래야 같은 지역인데 작물마다 데이터 유무가 들쭉날쭉해지는 문제가
    # 없다. 대체된 항목은 note에 "무엇으로 대체했는지"를 남겨 프론트가 막대 위
    # 마우스오버 툴팁으로만 보여주게 한다(항상 보이면 시각적으로 산만해서).
    own_category = LAND_USE_CATEGORY.get(crop, "Pfld")

    def fetch_soil_var(var, scorer):
        if not sigungu_full_name:
            return None, None
        value, used_category = soil.get_soil_variable_with_fallback(var, sigungu_full_name, crop)
        if value is None:
            return None, None
        score = scorer(crop, value)
        note = None
        if used_category != own_category:
            note = (
                f"이 지역엔 {soil.LAND_USE_LABELS[own_category]}(으)로 등록된 토양검정이 없어, "
                f"{soil.LAND_USE_LABELS[used_category]} 기준 값으로 대신 표시했어요(참고용)."
            )
        return score, note

    ph_score, ph_note = fetch_soil_var("pH", score_ph)
    om_score, om_note = fetch_soil_var("유기물", score_organic_matter)
    ap_score, ap_note = fetch_soil_var("유효인산", score_available_phosphate)
    ec_value = soil.get_ec(sigungu_full_name) if sigungu_full_name else None
    ec_score = score_ec(crop, ec_value) if ec_value is not None else None

    add("pH", ph_score, "흙토람(누적 통계)", ph_note)
    add("유기물", om_score, "흙토람(누적 통계)", om_note)
    add("유효인산", ap_score, "흙토람(누적 통계)", ap_note)
    add("EC", ec_score, "흙토람(누적 통계)")

    adjusted_weights, weight_coverage = _renormalize(weights, set(excluded))
    total_score = None
    if adjusted_weights:
        total_score = round(sum(breakdown[v]["score"] * adjusted_weights.get(v, 0) / 100 for v in breakdown), 1)
        for v in breakdown:
            breakdown[v]["weight"] = adjusted_weights.get(v, 0)

    return {
        "status": "matched",
        "input_region": region_name,
        "crop": crop,
        "matched_station": matched_station,
        "cultivation_type": cultivation_type,
        "distance_km": crop_match["distance_km"],
        "station_warning": crop_match.get("warning"),
        "total_score": total_score,
        "weight_coverage": round(weight_coverage * 100, 1),
        "breakdown": breakdown,
        "excluded_variables": excluded,
        "years_used": (normal_entry or {}).get("years_used"),
        "cluster_id": cluster_id,
        "cluster_name": cluster_name,
    }
