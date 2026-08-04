# -*- coding: utf-8 -*-
"""품종 적합도 채점 (breed.md §6).

작물 점수(live_scoring)와의 관계
  작물 점수는 "이 지역이 감자에 맞는가"를 연/생육기 평년값으로 본다. 이 모듈은 그 위에
  얹혀 "그 감자 중 **어느 품종**을, **어느 작형**으로, **언제 심어야** 하는가"를 본다.
  두 점수는 서로를 대체하지 않는다 - 화면에서도 "감자 84점 → 그중 추백 91점"으로 잇는다.

채점 단위: (지역 × 품종 × 작형 × 파종일)
  파종일을 고정하지 않고 **작형별로 5일 간격으로 훑어 가장 좋은 파종일을 찾는다.**
  품종 차이(추백 80~90일 / 자영 110일 이상)는 "언제 심어 언제 캐는가"로만 드러나므로,
  파종일을 고정해 버리면 만생종이 항상 불리해지는 가짜 결론이 나온다.

항목과 가중치 (합 100)
  재배기간 30 · 비대기온도 25 · 토양 15 · 강수/과습 12 · 병해 10 · 후기저온 8
  · 후기저온은 '색소·기능성 품종'에만 해당한다(자영). 해당 없으면 항목을 빼고
    나머지 가중치를 재정규화한다 - 없는 항목에 100점을 주면 그 품종만 유리해진다.
  · 난이도(초보 적합)는 점수에 넣지 않는다. 환경이 최적인데 점수가 깎이는 혼란을 막고,
    동점 정렬과 배지로만 쓴다(breed.md §6.5).

하드 게이트
  무상기간이 생육일수보다 짧으면 "조금 불리"가 아니라 실패다. 완만 감점으로 표현하지
  않고 종합 점수에 상한을 씌운다(20/40점). 상한이 걸리면 blockers에 사유가 담기고,
  챗봇은 점수보다 이걸 먼저 말하도록 되어 있다.
"""

import csv
import logging
from datetime import date
from pathlib import Path

import blight_data
import cultivar_data
import cultivar_reasons
import reference_data
import season_window
from scoring_engine import _binary_range_score, _linear_interpolate_beyond  # noqa: F401 (재사용)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
CLIMATE_CSV = BASE_DIR / "data" / "raw" / "climate_clustering_final_v3.csv"

# 항목과 가중치. 앞 6개는 모든 품종에 적용되고(합 100), 뒤 2개는 **그 품종의 재배 목적이
# 데이터에 적혀 있을 때만** 붙는다(붙으면 전체를 재정규화한다).
#   후기저온 - 색소·기능성 품종(자영): 후반이 서늘해야 상품성이 오른다
#   출하시기 - 조기출하용 품종(추백): 늦게 캐면 품종을 고른 이유가 사라진다
# 조건부 항목을 '해당 없으면 100점'으로 처리하지 않는 이유: 그러면 목적이 적히지 않은
# 품종이 공짜 만점을 받아 순위가 뒤집힌다.
WEIGHTS = {
    "재배기간": 28,
    "파종출현": 12,
    "비대온도": 24,
    "토양": 14,
    "강수": 12,
    "병해": 10,
    "후기저온": 8,
    "출하시기": 8,
}

# 작형별 파종일 탐색 범위와 서리 규칙.
#   frost_lead_days: 파종은 '마지막 봄서리 - N일'까지 앞당길 수 있다. 감자는 파종 후
#     20일쯤 땅속에 있어 늦서리를 피할 수 있기 때문이다(봄재배 관행).
#   require_before_fall_frost: 수확이 가을 첫 서리보다 이 일수만큼 앞서야 한다.
#   scan 범위는 그 작형의 **관행 파종기**다. 이 범위를 넓게 열어두면 탐색이 늘 늦은
#   쪽 끝을 고른다(늦게 심을수록 비대기가 서늘해지므로) - 실제로는 늦게 심으면 서리
#   전에 성숙하지 못하고 가을 강우·역병을 맞는다. 그 힘은 아래 서리여유 점수가
#   담당하고, 관행 범위는 애초에 상식 밖 파종일이 후보에 들지 않게 막는다.
SEASON_RULES = {
    cultivar_data.SEASON_SPRING:   {"scan": ("02-20", "05-15"), "frost_lead_days": 21, "harvest_margin": 3},
    cultivar_data.SEASON_HIGHLAND: {"scan": ("04-20", "06-15"), "frost_lead_days": 7,  "harvest_margin": 5},
    # 가을재배 파종기는 8월이다. 7월에 심는 것은 가을재배가 아니라 여름재배이고,
    # 실제로 범위를 7월 초까지 열었을 때 탐색이 남부 가을재배를 7월 5일 파종으로
    # 골랐다(장마 직격 + 폭염 파종). 관행 범위로 좁혀 상식 밖 후보를 배제한다.
    cultivar_data.SEASON_FALL:     {"scan": ("08-01", "09-10"), "frost_lead_days": None, "harvest_margin": 5},
}

# 파종기 고온 게이트. 봄·고랭지 작형은 파종기가 더울 이유가 없으므로 엄하게 막고,
# 가을재배는 '더운 때 심는 것'이 작형의 전제라 극단값만 막고 주의로 안내한다
# (남부·제주 가을감자는 8월 파종이 관행이다 - 여기에 봄재배 기준을 대면 실재하는
#  주력 작형이 전 지역에서 사라진다).
EMERGENCE_GATES = {
    "기본": ((28, 45), (26, 60)),
    cultivar_data.SEASON_FALL: ((29, 55),),
}
EMERGENCE_CAUTION_C = 25
SCAN_STEP_DAYS = 5

# 고랭지 작형이 성립하는 지역 조건. 표고 400m 이상이거나 기후 클러스터가 고랭지형(2)인
# 지역만 후보로 둔다. 평지에 '고랭지 여름재배'를 권하면 한여름 비대기 고온을 그대로 맞는다.
# ⚠️ 중산간내륙형(0)은 고랭지가 아니다 - 이 클러스터에는 충주(표고 115m)처럼 평난지가
#    24개소나 들어 있어(clustering.py K=6 결과) 여기까지 열면 평지에 고랭지 작형을 권한다.
HIGHLAND_MIN_ELEVATION_M = 400
HIGHLAND_CLUSTER_IDS = (2,)

# 시설재배는 노지 기상으로 채점할 수 없다(온도를 사람이 만든다) - 후보에서 제외하고
# 서술로만 안내한다.
SCORABLE_SEASONS = tuple(SEASON_RULES)

_climate_cache = {}


# ═══════════════════════════════════════════════════════════════
# 1. 지역 부가정보 (표고·습도)
# ═══════════════════════════════════════════════════════════════

def _climate_row(station_id):
    """climate_clustering_final_v3.csv에서 관측소 행(표고·습도 등)을 찾는다."""
    if not _climate_cache:
        try:
            with open(CLIMATE_CSV, encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    _climate_cache[str(row["station_id"])] = row
        except Exception as e:                                    # noqa: BLE001
            logger.error("[cultivar_fit] 기후 CSV 로드 실패: %s", e)
            _climate_cache["__failed__"] = {}
    return _climate_cache.get(str(station_id))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════
# 2. 항목별 점수 함수
# ═══════════════════════════════════════════════════════════════

def _piecewise(value, points):
    """points=[(x, score), ...] (x 오름차순) 사이를 선형 보간. 양 끝은 그 값으로 고정."""
    if value is None:
        return None
    if value <= points[0][0]:
        return points[0][1]
    for (x0, s0), (x1, s1) in zip(points, points[1:]):
        if value <= x1:
            if x1 == x0:
                return s1
            return s0 + (s1 - s0) * (value - x0) / (x1 - x0)
    return points[-1][1]


def score_growing_period(frost_free_days, need_days):
    """재배기간 확보: 무상기간이 생육일수를 여유 있게 넘는가.

    여유 25일 이상이면 만점, 여유가 사라질수록 급히 떨어진다. 게이트와 별개로
    '점수'로도 표현해 두어야 같은 등급 안에서 순위가 갈린다.
    """
    if frost_free_days is None or need_days is None:
        return None, None
    margin = frost_free_days - need_days
    score = _piecewise(margin, [(-20, 5), (-10, 15), (0, 45), (10, 80), (25, 100), (60, 100)])
    return score, margin


def score_frost_slack(slack_days):
    """수확 예정일이 가을 첫 서리보다 며칠 앞서는가.

    이 항목이 없으면 탐색이 늘 '가능한 가장 늦은 파종일'을 고른다 - 늦게 심을수록
    비대기가 서늘해져 온도 점수가 올라가기 때문이다. 실제 농사에서 그 대가는 서리와
    가을장마이므로, 여유가 줄어드는 것을 점수로 갚게 한다. 무상기간 여유와 함께
    '재배기간' 항목의 두 축이며, 두 값 중 **나쁜 쪽**을 항목 점수로 쓴다.
    """
    if slack_days is None:
        return None
    return _piecewise(slack_days, [(0, 35), (7, 55), (15, 75), (30, 100), (120, 100)])


def score_bulking_temp(bulking_mean, lo, hi, hot_days, hot_days_severe, heat_sensitive):
    """비대·결실기 온도: 적온 범위 + 고온일수 감점.

    고온일수를 온도 항목 안에서 처리하는 이유 - 같은 원인(더위)을 온도와 별도 항목으로
    두 번 깎으면 더운 지역의 만생종이 이중으로 벌점을 받는다.
    """
    if bulking_mean is None or lo is None:
        return None, {}
    if lo <= bulking_mean <= hi:
        base = 100.0
    else:
        dist = (lo - bulking_mean) if bulking_mean < lo else (bulking_mean - hi)
        base = max(25.0, 100.0 - (dist / 4.0) * 45.0)     # 4℃ 벗어나면 55점, 이후 25점 바닥

    # 감점 계수는 "적온을 벗어난 정도"와 "고온일수"를 이중으로 깎지 않게 낮게 잡았다.
    # 25℃ 초과는 비대 둔화가 '시작'되는 값이고(작물표준 high_temp_risk의 note), 우리
    # 봄감자 주산지에서도 비대 후기에 흔하다 - 여기에 큰 계수를 쓰면 국내 표준 재배법인
    # 봄재배가 전 지역에서 '위험'으로 나온다(초기 보정에서 실제로 그렇게 나왔다).
    # 정지 수준인 30℃ 초과일수에 더 큰 계수를 준다.
    penalty = (hot_days_severe or 0) * 2.5 + (hot_days or 0) * 0.3
    if heat_sensitive:
        penalty *= 1.4                                    # 고온이 원인인 생리장해가 있는 품종
    penalty = min(penalty, 30.0)
    return max(0.0, base - penalty), {
        "적온범위": f"{lo}~{hi}℃", "비대기평균": bulking_mean,
        "고온일수": hot_days, "30℃초과일수": hot_days_severe,
        "고온감점": round(penalty, 1),
    }


def score_emergence(emergence_mean_temp):
    """파종~출현기(약 20일) 평균기온: 씨감자가 썩지도, 싹이 늦지도 않는 구간인가.

    감자는 5℃부터 싹이 자라고(작물표준 sprouting_min), 파종기가 더우면 절단면으로
    세균이 들어가 씨감자가 썩는다. 이 항목이 없으면 탐색이 '가능한 가장 이른/늦은'
    파종일을 고른다 - 실제로 제주 가을재배를 7월 하순(평균 27℃)에 심으라는 결과가
    나왔다. 파종기 조건은 작기 성패를 가르는 축이라 별도 항목으로 둔다.
    """
    return _piecewise(emergence_mean_temp, [
        (2, 20), (5, 45), (8, 80), (10, 100), (22, 100), (25, 65), (27, 40), (30, 15),
    ])


def score_early_market(harvest_mmdd):
    """조기출하용 품종의 수확 시점: 노지감자 성출하기 전에 캐는가.

    추백을 7월에 캔다면 극조생종을 고른 이유(값을 더 받는 조기 출하)가 사라진다.
    이 항목은 '조기 출하'가 데이터에 목적으로 적힌 품종에만 적용한다.
    """
    if not harvest_mmdd:
        return None
    doy = date(2001, int(harvest_mmdd[:2]), int(harvest_mmdd[3:5])).timetuple().tm_yday
    return _piecewise(doy, [
        (date(2001, 5, 20).timetuple().tm_yday, 100),
        (date(2001, 6, 10).timetuple().tm_yday, 100),
        (date(2001, 6, 25).timetuple().tm_yday, 75),
        (date(2001, 7, 10).timetuple().tm_yday, 50),
        (date(2001, 7, 25).timetuple().tm_yday, 30),
        (date(2001, 12, 31).timetuple().tm_yday, 30),
    ])


def score_soil(readings, ph_lo, ph_hi, crop):
    """토양: pH(품종/작물 기준) + 유기물·유효인산(작물 기준)의 가중 평균.

    셋 다 결측이면 None을 돌려 항목 자체를 빼게 한다(0점 처리 금지).
    """
    parts, detail = [], {}
    ph = readings.get("pH") if readings else None
    if ph is not None and ph_lo is not None:
        s = _binary_range_score(ph, ph_lo, ph_hi, buffer_ratio=0.3)
        parts.append((s, 0.6))
        detail["pH"] = {"값": round(ph, 2), "기준": f"{ph_lo}~{ph_hi}", "점수": round(s)}

    om = readings.get("유기물") if readings else None
    om_std = getattr(reference_data, "ORGANIC_MATTER_THRESHOLDS", {}).get(crop)
    if om is not None and om_std:
        s = _binary_range_score(om, om_std["min"], om_std["max"], buffer_ratio=0.3)
        parts.append((s, 0.2))
        detail["유기물"] = {"값": round(om, 1), "기준": f"{om_std['min']}~{om_std['max']}", "점수": round(s)}

    ap = readings.get("유효인산") if readings else None
    ap_std = getattr(reference_data, "AVAILABLE_PHOSPHATE_THRESHOLDS", {}).get(crop)
    if ap is not None and ap_std:
        s = _binary_range_score(ap, ap_std["min"], ap_std["max"], buffer_ratio=0.3)
        parts.append((s, 0.2))
        detail["유효인산"] = {"값": round(ap, 1), "기준": f"{ap_std['min']}~{ap_std['max']}", "점수": round(s)}

    if not parts:
        return None, {}
    total_w = sum(w for _, w in parts)
    return sum(s * w for s, w in parts) / total_w, detail


def score_rain(window_rain_mm, heavy_rain_days, window_days, crop, season):
    """강수: '부족'과 '과습'을 각각 보고 나쁜 쪽을 취한다.

    부족 판정은 기존 기준값(reference_data.PRECIP_THRESHOLDS)을 쓰되, 그 기준이 정해진
    생육기간 길이에 맞춰 **작기 길이로 비례 조정**한다. 감자 봄재배 기준(3/1~6/30,
    122일)의 256mm를 90일 작기에 그대로 대면 항상 부족으로 나온다.
    과습은 기준값이 없어 집중강수일수(50mm 이상)로 본다 - 감자는 침수에 매우 약하고
    (수확기 24시간 침수면 부패 시작) 총량보다 한 번에 오는 비가 문제다.
    """
    if window_rain_mm is None:
        return None, {}

    ref_type = season if season in (reference_data.PRECIP_THRESHOLDS.get(crop) or {}) else None
    if ref_type is None:
        # 가을재배 등 기존 엔진에 없는 작형은 봄재배 기준을 쓴다(근거를 응답에 밝힌다).
        ref_type = next(iter(reference_data.PRECIP_THRESHOLDS.get(crop, {})), None)
    detail = {"작기강수mm": window_rain_mm, "집중강수일수": heavy_rain_days, "기준작형": ref_type}

    shortage = 100.0
    if ref_type:
        th = reference_data.PRECIP_THRESHOLDS[crop][ref_type]
        periods = reference_data.GROWTH_PERIOD.get(crop, {}).get(ref_type) or []
        ref_days = 0
        for (sm, sd), (em, ed) in periods:
            ref_days += (date(2001, em, ed) - date(2001, sm, sd)).days + 1
        scale = (window_days / ref_days) if ref_days else 1.0
        near, danger = th["near"] * scale, th["danger"] * scale
        shortage = _linear_interpolate_beyond(window_rain_mm, near, danger, danger_score=30)
        detail["부족기준"] = {"near": round(near), "danger": round(danger)}

    excess_penalty = max(0.0, (heavy_rain_days or 0) - 2) * 9.0
    detail["과습감점"] = round(excess_penalty, 1)
    return max(0.0, min(shortage, 100.0 - excess_penalty)), detail


def score_disease(diseases, heavy_rain_days, humidity):
    """병해 위험: 품종의 감수성 × 지역의 습한 정도.

    바이러스병은 강수·습도가 아니라 씨감자와 진딧물에서 온다 - 지역 습도로 가중하지
    않고 고정 감점으로 둔다(추백의 PVY 감수성이 건조한 지역에서 사라지지는 않는다).
    """
    wet = 1.0
    if (heavy_rain_days or 0) >= 4 or (humidity or 0) >= 72:
        wet = 1.4
    elif (heavy_rain_days or 0) < 2 and (humidity or 0) and humidity < 68:
        wet = 0.7

    penalty, notes = 0.0, []
    for d in diseases or []:
        base = {"높음": 10.0, "중간": 5.0, "낮음": 1.0}.get(d.get("level"), 5.0)
        is_virus = "바이러스" in (d.get("name") or "")
        p = base if is_virus else base * wet
        penalty += p
        notes.append(f"{d.get('name')}({d.get('level_raw') or d.get('level')})")
    penalty = min(penalty, 45.0)
    return 100.0 - penalty, {"습윤계수": wet, "감점": round(penalty, 1), "대상": notes}


def score_late_cool(late_delta, bulking_mean):
    """후기 저온(색소·기능성 품종만): 생육 후반이 앞구간보다 서늘한가.

    자영은 '수확 전 기온이 낮을수록 안토시아닌 축적에 유리'가 데이터에 근거로 있다.
    성분 함량을 예측하지는 않는다 - 유리/불리 방향만 점수로 바꾼다.
    """
    if late_delta is None:
        return None, {}
    score = _piecewise(late_delta, [(-6, 100), (-2, 100), (0, 75), (2, 50), (6, 40)])
    if bulking_mean is not None and bulking_mean > 20:
        score = min(score, 45.0)                     # 아무리 '내려가는 중'이어도 절대적으로 더우면 불리
    return score, {"후기기온차": late_delta, "비대기평균": bulking_mean}


# ═══════════════════════════════════════════════════════════════
# 3. 작형·파종일 탐색
# ═══════════════════════════════════════════════════════════════

def _season_growth_days(variety, season):
    """작형별 생육일수(대서만 작형별로 다름). (min, max)."""
    by_season = variety.get("growth_days_by_season") or {}
    if season in by_season:
        lo, hi = by_season[season]
        return lo, hi
    gd = variety.get("growth_days") or {}
    return gd.get("min"), gd.get("max")


def _scan_planting_dates(season, frost):
    """작형 규칙 + 지역 서리일로 실제 가능한 파종일 후보를 만든다."""
    rule = SEASON_RULES[season]
    start, end = rule["scan"]
    earliest = start
    if rule["frost_lead_days"] is not None and frost.get("last_spring"):
        frost_gate = season_window.mmdd_add(frost["last_spring"], -rule["frost_lead_days"])
        if season_window.mmdd_diff(frost_gate, earliest) > 0:
            earliest = frost_gate

    out, cur = [], earliest
    while season_window.mmdd_diff(end, cur) >= 0:
        out.append(cur)
        cur = season_window.mmdd_add(cur, SCAN_STEP_DAYS)
    return out


def _harvest_fits_fall_frost(plant, days, season, frost):
    """수확이 가을 첫 서리 앞에 들어오는가. (들어오면 True, 남는 일수)"""
    if not frost.get("first_fall") or days is None:
        return True, None
    harvest = season_window.mmdd_add(plant, int(days))
    slack = season_window.mmdd_diff(frost["first_fall"], harvest) - SEASON_RULES[season]["harvest_margin"]
    return slack >= 0, slack


def _feasible_seasons(variety, region):
    """이 품종 × 이 지역에서 채점할 작형과, 제외한 작형의 사유."""
    seasons = [s for s in (variety.get("seasons") or []) if s in SCORABLE_SEASONS]
    if not seasons:
        seasons = [cultivar_data.SEASON_SPRING]         # 작형 서술이 없으면 봄재배만 본다
    excluded = {}

    for s in list(variety.get("seasons_excluded") or []):
        if s in seasons:
            seasons.remove(s)
            excluded[s] = "품종 데이터에서 권장하지 않는 작형"

    if cultivar_data.SEASON_HIGHLAND in seasons:
        elev = region.get("elevation_m")
        cluster = region.get("cluster_id")
        if not ((elev is not None and elev >= HIGHLAND_MIN_ELEVATION_M)
                or cluster in HIGHLAND_CLUSTER_IDS):
            seasons.remove(cultivar_data.SEASON_HIGHLAND)
            excluded[cultivar_data.SEASON_HIGHLAND] = (
                f"이 지역 표고({'?' if elev is None else int(elev)}m)에서는 고랭지 작형이 성립하지 않음"
            )
    return seasons, excluded


# ═══════════════════════════════════════════════════════════════
# 4. 품종 1개 채점
# ═══════════════════════════════════════════════════════════════

def _score_candidate(variety, season, plant, days, clim, region, soil_readings):
    """(품종, 작형, 파종일) 하나를 채점한다. 반환 None이면 후보 자체가 성립하지 않음."""
    frost = clim["frost"]
    fits, slack = _harvest_fits_fall_frost(plant, days, season, frost)
    if not fits:
        return None

    metrics = season_window.window_metrics(
        clim, plant, days,
        hot_threshold=variety.get("hot_threshold") or 25.0,
    )
    if not metrics:
        return None

    items, detail = {}, {}

    # 재배기간 = ① 무상기간이 생육일수를 넘는가 ② 수확이 첫서리보다 넉넉히 앞서는가.
    # 둘 중 나쁜 쪽을 쓴다(하나만 보면 늦은 파종이 공짜가 된다).
    period_score, margin = score_growing_period(frost.get("frost_free_days"), days)
    slack_score = score_frost_slack(slack)
    items["재배기간"] = (min(period_score, slack_score)
                     if (period_score is not None and slack_score is not None) else period_score)
    detail["재배기간"] = {
        "무상기간일": frost.get("frost_free_days"), "필요일수": days, "여유일수": margin,
        "첫서리까지여유일": (slack + SEASON_RULES[season]["harvest_margin"]) if slack is not None else None,
        "무상기간점수": round(period_score) if period_score is not None else None,
        "서리여유점수": round(slack_score) if slack_score is not None else None,
    }

    items["파종출현"] = score_emergence(metrics.get("emergence_mean_temp"))
    detail["파종출현"] = {"출현기평균기온": metrics.get("emergence_mean_temp"),
                       "적정": "10~22℃ (5℃ 미만 출현 지연 · 25℃ 초과 씨감자 부패 위험)"}

    s, d = score_bulking_temp(
        metrics["bulking_mean_temp"],
        (variety["bulking_temp"] or {}).get("min"), (variety["bulking_temp"] or {}).get("max"),
        metrics["hot_days"], metrics["hot_days_severe"], variety.get("heat_disorder_sensitive"),
    )
    items["비대온도"], detail["비대온도"] = s, d

    s, d = score_soil(soil_readings, variety["soil_ph"].get("min"), variety["soil_ph"].get("max"), variety["crop"])
    items["토양"], detail["토양"] = s, d

    s, d = score_rain(metrics["window_rain_mm"], metrics["heavy_rain_days"], days, variety["crop"], season)
    items["강수"], detail["강수"] = s, d

    s, d = score_disease(variety.get("diseases"), metrics["heavy_rain_days"], region.get("humidity"))
    items["병해"], detail["병해"] = s, d

    if variety.get("late_cool_preferred"):
        s, d = score_late_cool(metrics["late_delta"], metrics["bulking_mean_temp"])
        items["후기저온"], detail["후기저온"] = s, d
    else:
        items["후기저온"] = None
        detail["후기저온"] = {"해당없음": "색소·기능성 품종에만 적용하는 항목"}

    if variety.get("early_market_preferred"):
        items["출하시기"] = score_early_market(metrics.get("harvest"))
        detail["출하시기"] = {"수확예정": metrics.get("harvest"),
                          "기준": "6월 상순까지 100점 · 노지감자 성출하기(7월)로 갈수록 감점"}
    else:
        items["출하시기"] = None
        detail["출하시기"] = {"해당없음": "조기출하가 목적인 품종에만 적용하는 항목"}

    # 결측 항목은 빼고 가중치 재정규화 (reading_guard와 같은 원칙)
    used = {k: v for k, v in items.items() if v is not None}
    excluded_items = [k for k, v in items.items() if v is None]
    total_w = sum(WEIGHTS[k] for k in used)
    raw_score = sum(used[k] * WEIGHTS[k] for k in used) / total_w if total_w else None
    if raw_score is None:
        return None

    # ── 하드 게이트 ──
    caps, blockers, cautions = [], [], []
    if margin is not None:
        if margin < 0:
            caps.append(20)
            blockers.append(
                f"무상기간 {frost.get('frost_free_days')}일 < 필요 생육기간 {days}일 — "
                f"이 작형으로는 서리 전에 캘 수 없어요"
            )
        elif margin < 10:
            caps.append(40)
            cautions.append(
                f"여유가 {margin}일뿐이라 늦서리·첫서리가 이르면 생육기간이 부족해질 수 있어요"
            )
    # 파종기 조건도 게이트다. 점수 감점만으로는 탐색이 한여름 파종(제주 가을재배를
    # 7월 하순에 심으라는 결과)을 계속 골랐다 - 씨감자 부패는 '조금 불리'가 아니라 결주다.
    e_temp = metrics.get("emergence_mean_temp")
    if e_temp is not None:
        for threshold, cap in EMERGENCE_GATES.get(season, EMERGENCE_GATES["기본"]):
            if e_temp >= threshold:
                caps.append(cap)
                cautions.append(
                    f"파종 직후 20일 평균이 {e_temp}℃로 매우 높아 씨감자가 썩을 위험이 커요 — 파종을 늦추세요"
                )
                break
        else:
            if e_temp >= EMERGENCE_CAUTION_C:
                cautions.append(
                    f"파종 직후 20일 평균이 {e_temp}℃예요 — 절단면을 충분히 치유하고 젖은 밭 파종을 피하세요"
                )
        if e_temp < 5:
            caps.append(65)
            cautions.append(f"파종 직후 20일 평균이 {e_temp}℃로 낮아 출현이 늦어질 수 있어요")
    if metrics["frost_days_after_emergence"] >= 3:
        caps.append(60)
        cautions.append(
            f"출현 이후 서리가 평년 {metrics['frost_days_after_emergence']}일 있어 파종을 늦추는 편이 안전해요"
        )
    if slack is not None and 0 <= slack < 7:
        cautions.append(f"수확 예정일이 첫서리({frost['first_fall']})와 {slack + SEASON_RULES[season]['harvest_margin']}일 차이예요")
    # 근거값이 없는 작형은 대리 기준을 썼다는 사실을 숨기지 않는다(가을재배는 기존
    # 점수엔진에 감자 강수 기준값이 없다 - reference_data는 봄재배·고랭지재배만 있다).
    ref_season = (detail.get("강수") or {}).get("기준작형")
    if ref_season and ref_season != season:
        cautions.append(f"{season} 강수 기준값이 없어 {ref_season} 기준을 작기 길이에 맞춰 환산했어요(참고용)")

    score = min([raw_score] + caps) if caps else raw_score

    return {
        "season": season,
        "plant": plant,
        "harvest": metrics["harvest"],
        "days": days,
        "score": round(score, 1),
        "raw_score": round(raw_score, 1),
        "caps": caps,
        "items": {k: (round(v, 1) if v is not None else None) for k, v in items.items()},
        "weights": {k: WEIGHTS[k] for k in used},
        "excluded_items": excluded_items,
        "detail": detail,
        "metrics": metrics,
        "blockers": blockers,
        "cautions": cautions,
    }


def _best_for_season(variety, season, clim, region, soil_readings):
    """작형 하나에서 파종일을 훑어 가장 좋은 후보를 고른다."""
    lo, hi = _season_growth_days(variety, season)
    if lo is None:
        return None
    # 생육일수는 '최소'를 기준으로 잡는다. 만생종의 max가 없거나(자영 110~null) 넓을 때
    # max로 잡으면 실제보다 훨씬 불리하게 나온다. 대신 여유 판정(게이트)이 최소값을 쓴다.
    days = int(lo)

    best, tried = None, 0
    for plant in _scan_planting_dates(season, clim["frost"]):
        cand = _score_candidate(variety, season, plant, days, clim, region, soil_readings)
        tried += 1
        if cand and (best is None or cand["score"] > best["score"]):
            best = cand
    if best is None:
        return None
    best["scan_count"] = tried
    best["days_range"] = {"min": lo, "max": hi}
    return best


# ═══════════════════════════════════════════════════════════════
# 5. 근거 문장 (LLM 아님 - 계산값으로 만든다)
# ═══════════════════════════════════════════════════════════════

def _reasons(variety, best):
    """근거 문장. 화면은 첫 문장만 보여주므로 **품종을 구분해 주는 문장을 앞에** 둔다.

    무상기간 문장은 여유가 넉넉하면 모든 품종에서 똑같이 나와(카드 4장이 전부 "무상기간
    198일로…") 아무것도 알려주지 않는다. 여유가 빠듯할 때만 앞으로 올린다.
    """
    m, d = best["metrics"], best["detail"]
    out = []

    margin = (d.get("재배기간") or {}).get("여유일수")
    period_line = None
    if margin is not None and margin >= 0:
        period_line = (
            f"무상기간 {d['재배기간']['무상기간일']}일로 {variety['name']}에 필요한 "
            f"{best['days']}일을 여유 {margin}일로 넘겨요"
        )
        if margin < 30:                     # 빠듯하면 이게 가장 중요한 정보다
            out.append(period_line)
            period_line = None
    slack = (d.get("재배기간") or {}).get("첫서리까지여유일")
    if slack is not None and slack < 20:
        out.append(f"수확 예정일({best.get('harvest')})이 첫서리까지 {slack}일밖에 안 남아요")
    bt = variety.get("bulking_temp") or {}
    if m.get("bulking_mean_temp") is not None and bt.get("min") is not None:
        inside = bt["min"] <= m["bulking_mean_temp"] <= (bt["max"] or bt["min"])
        out.append(
            f"비대기 평균 {m['bulking_mean_temp']}℃가 적온({bt['min']}~{bt['max']}℃)"
            + ("에 들어요" if inside else "에서 벗어나요")
        )
    if variety.get("late_cool_preferred") and m.get("late_delta") is not None:
        if m["late_delta"] <= -1:
            out.append(f"생육 후반이 {abs(m['late_delta'])}℃ 더 서늘해 색이 진해지는 방향이에요")
        elif m["late_delta"] >= 1:
            out.append(f"생육 후반이 {m['late_delta']}℃ 더 더워 색 발현에는 불리한 방향이에요")
    if m.get("emergence_mean_temp") is not None and m["emergence_mean_temp"] >= 24:
        out.append(f"파종 직후 20일 평균이 {m['emergence_mean_temp']}℃로 높아 씨감자 부패에 주의해야 해요")
    if variety.get("early_market_preferred") and best.get("harvest"):
        out.append(f"수확이 {best['harvest']}로 " +
                   ("조기 출하 시기에 들어와요" if best["harvest"] <= "06-20" else "조기 출하 시기보다 늦어요"))
    if m.get("hot_days_severe", 0) >= 3:
        out.append(f"비대기에 30℃ 넘는 날이 평년 {m['hot_days_severe']}일 있어 비대가 둔해질 수 있어요")
    if m.get("heavy_rain_days", 0) >= 3:
        out.append(f"작기 중 하루 50mm 이상 비가 평년 {m['heavy_rain_days']}일 있어 배수로 정비가 필요해요")
    if period_line:                          # 여유가 넉넉한 무상기간 문장은 맨 뒤로
        out.append(period_line)
    return out[:4]


# ═══════════════════════════════════════════════════════════════
# 6. 공개 API
# ═══════════════════════════════════════════════════════════════

def score_cultivars(region_name, crop="감자", experience="beginner", years=season_window.DEFAULT_YEARS,
                    allow_fetch=True, climatology=None, soil_readings=None, station=None,
                    matched_region=None):
    """지역+작물의 품종 순위. 반환은 breed.md §7.2 형태.

    climatology/soil_readings/station을 주면 외부 호출 없이 계산한다(테스트용 주입점).
    """
    varieties = cultivar_data.list_varieties(crop)
    if not varieties:
        return {"error": f"품종 데이터가 아직 없어요: {crop}",
                "available_crops": cultivar_data.available_crops()}

    # ── 지역 → 관측소 ──
    if station is None:
        from region_mapper import find_nearest_station          # noqa: E402 (지연 import: 테스트 주입 시 불필요)
        m = find_nearest_station(region_name)
        if m.get("status") != "matched":
            return {"status": m.get("status", "not_found"), "region": region_name, "crop": crop,
                    "error": f"지역을 찾지 못했어요: {region_name} ({m.get('status')})"}
        station = m["station"]
        matched_region = m.get("matched_region") or {}
        distance_km = m.get("distance_km")
    else:
        matched_region = matched_region or {}
        distance_km = None

    row = _climate_row(station["station_id"]) or {}
    region = {
        "station_id": station["station_id"],
        "station_name": station.get("station_name"),
        "cluster_id": station.get("cluster_id"),
        "cluster_name": station.get("cluster_name"),
        "distance_km": distance_km,
        "elevation_m": _num(row.get("elevation")),
        "humidity": _num(row.get("humidity")),
        "annual_precip": _num(row.get("annual_precip")),
        "sigungu_name": matched_region.get("sigungu_name"),
    }

    # ── 기상 기후값(무상기간·작기 통계) ──
    if climatology is None:
        climatology = season_window.station_climatology(
            station["station_id"], years=years, allow_fetch=allow_fetch
        )
    if not climatology or climatology.get("status") != "ok":
        return {
            "status": "no_climate", "region": region_name, "crop": crop, "region_metrics": region,
            "error": (climatology or {}).get("message")
                     or "이 지역의 과거 기상자료(ASOS)를 확보하지 못해 품종 판정을 할 수 없어요",
        }

    # ── 토양 (있으면 반영, 없으면 항목 제외) ──
    soil_note = None
    if soil_readings is None:
        try:
            import soil                                          # noqa: E402
            soil_readings = soil.get_soil_readings(region["sigungu_name"], crop) if region["sigungu_name"] else {}
        except Exception as e:                                    # noqa: BLE001
            logger.error("[cultivar_fit] 흙토람 조회 실패: %s", e)
            soil_readings, soil_note = {}, f"토양 조회 실패({e})"

    ranking, skipped = [], []
    for v in varieties:
        seasons, excluded_seasons = _feasible_seasons(v, region)
        per_season = {}
        for s in seasons:
            best = _best_for_season(v, s, climatology, region, soil_readings)
            if best:
                per_season[s] = best
            else:
                excluded_seasons.setdefault(s, "서리 전에 수확이 끝나는 파종일이 없음")

        if not per_season:
            skipped.append({
                "cultivar": v["name"], "reason": "이 지역에서 성립하는 작형이 없어요",
                "excluded_seasons": excluded_seasons,
                "key_warnings": v.get("key_warnings", [])[:2],
            })
            continue

        best = max(per_season.values(), key=lambda c: c["score"])
        grade, grade_label = _grade_of(best["score"])
        badges = []
        if experience == "beginner" and v.get("beginner_friendly") is False:
            badges.append("초보자에겐 소규모 시험재배 권장")
        if v.get("beginner_friendly"):
            badges.append("초보자에게 무난")

        # 화면의 '추천 이유 / 고려할 점'. 지역 근거를 맨 앞에 세운다 - 사용자가 궁금한
        # 것은 "왜 **우리 동네에서** 이 품종인가"이고, 그 답이 이 엔진의 존재 이유다.
        # 점수·breakdown 은 손대지 않는다(키만 추가한다).
        ffd = climatology["frost"]["frost_free_days"]
        region_pros = [(
            f"이 지역에서 {cultivar_reasons.with_particle(best['season'])} "
            f"{best['plant'].replace('-', '/')} 파종 → "
            f"{best['harvest'].replace('-', '/')} 수확이 성립해요"
            f" (무상기간 {ffd}일 / 필요 {best['days']}일)"
        )]
        blight = blight_data.blight_info(crop, v["name"])
        pros, cons_list = cultivar_reasons.build(
            v, region_pros=region_pros,
            region_cons=list(best["blockers"]) + list(best["cautions"]),
            blight=blight, experience=experience)

        ranking.append({
            "cultivar": v["name"],
            "aliases": v["aliases"],
            "maturity": v["maturity"],
            "category": v["category"],
            "score": best["score"],
            "grade": grade, "grade_label": grade_label,
            "cultivation_type": best["season"],
            "planting_window": {
                "best": best["plant"],
                "from": season_window.mmdd_add(best["plant"], -SCAN_STEP_DAYS),
                "to": season_window.mmdd_add(best["plant"], SCAN_STEP_DAYS),
                "harvest": best["harvest"],
                "days": best["days"],
            },
            "breakdown": {
                k: {"점수": best["items"][k], "가중치": WEIGHTS[k], "근거": best["detail"].get(k)}
                for k in WEIGHTS if best["items"].get(k) is not None
            },
            "excluded_items": best["excluded_items"],
            "blockers": best["blockers"],
            # 계산으로 나온 주의(이 지역·이 파종일이라서 생긴 것)와 품종 자체의 일반
            # 주의사항을 섞지 않는다. 섞으면 챗봇이 '재배기간 부족에 주의'(품종 일반
            # 경고)를 이 지역의 판정 결과처럼 말한다.
            "cautions": best["cautions"],
            "variety_warnings": v.get("key_warnings", [])[:3],
            "badges": badges,
            "pros": pros,
            "cons": cons_list,
            "late_blight": blight,
            "reasons": _reasons(v, best),
            "by_season": {
                s: {"score": c["score"], "plant": c["plant"], "harvest": c["harvest"], "days": c["days"]}
                for s, c in per_season.items()
            },
            "excluded_seasons": excluded_seasons,
            "beginner_friendly": v.get("beginner_friendly"),
            "beginner_reason": v.get("beginner_reason"),
            "primary_use": v.get("primary_use"),
            "report": v.get("report"),
        })

    # 동점(±2점)은 초보 적합 → 생육일수 짧은 순으로 (난이도는 점수에 넣지 않는다)
    ranking.sort(key=lambda r: (-round(r["score"] / 2), not r["beginner_friendly"],
                                r["planting_window"]["days"]))

    reliability, reason = _reliability(climatology, soil_readings, soil_note)

    region_cautions = list(cultivar_data.dataset_cautions(crop))
    # 관측소가 멀면 산간 농지의 실제 기온이 더 낮다 - 무상기간·비대기 기온이 전부
    # 관측소 기준이므로 이 사실을 숨기면 고랭지 농가가 잘못된 파종일을 받는다.
    if distance_km and distance_km >= 10:
        region_cautions.insert(0, (
            f"기준 관측소({region['station_name']})가 {distance_km}km 떨어져 있어요. 농지가 더 높은 곳이면 "
            f"서리가 이르고 기온이 낮으니 파종·수확을 실제 밭 기준으로 조정하세요"
        ))
    return {
        "status": "matched",
        "crop": crop,
        "region": region_name,
        "experience": experience,
        "region_metrics": {
            **region,
            "frost_free_days": climatology["frost"]["frost_free_days"],
            "frost_free_median": climatology["frost"]["frost_free_median"],
            "frost_free_note": climatology["frost"]["frost_free_note"],
            "last_spring_frost": climatology["frost"]["last_spring"],
            "first_fall_frost": climatology["frost"]["first_fall"],
            "years_used": climatology["frost"]["years_used"],
        },
        "soil_readings": {k: (round(v, 2) if isinstance(v, (int, float)) else v)
                          for k, v in (soil_readings or {}).items()},
        "ranking": ranking,
        "skipped": skipped,
        "reliability": reliability,
        "reliability_reason": reason,
        "cautions": region_cautions,
        "data_sources": {
            "기상": f"기상청 ASOS 일자료 {climatology['frost']['years_used']}년 평년(관측소 {region['station_name']})",
            "토양": "흙토람 SoilExamStat V2" + (f" — {soil_note}" if soil_note else ""),
            "품종": (cultivar_data.load_crop(crop) or {}).get("source_file"),
            "표고·습도": "data/raw/climate_clustering_final_v3.csv",
        },
    }


def _grade_of(score):
    """For_Frontend.md §3과 같은 4단계 매핑을 쓴다(화면 라벨을 통일하기 위해)."""
    if score is None:
        return None, "산출 불가"
    if score >= 80:
        return "good", "우수"
    if score >= 60:
        return "normal", "양호"
    if score >= 40:
        return "caution", "주의"
    return "bad", "위험"


def _reliability(clim, soil_readings, soil_note):
    problems = []
    years = clim["frost"]["years_used"]
    if years < 8:
        problems.append(f"기상 {years}년치만 사용")
    missing = [k for k in ("pH", "유기물", "유효인산") if not (soil_readings or {}).get(k)]
    if missing:
        problems.append("토양 결측: " + ", ".join(missing))
    if soil_note:
        problems.append(soil_note)
    if not problems:
        return "정상", None
    if years < 5 or len(missing) == 3:
        return "신뢰불가", " · ".join(problems)
    return "주의", " · ".join(problems)


def cultivar_profile(crop, name, topic=None):
    """품종 1개의 상세. topic으로 섹션을 좁힌다(챗봇 축약용)."""
    v = cultivar_data.find_variety(crop, name)
    if not v:
        return {"error": f"품종 데이터가 아직 없어요: {crop} '{name}'",
                "available": cultivar_data.variety_names(crop)}

    common = (cultivar_data.load_crop(crop) or {}).get("common_management", {})
    sections = {
        "개요": {
            "숙기": v["maturity"], "생육기간": v["growth_days"], "용도": v["primary_use"],
            "특징": v["headline"], "괴경특성": v["tuber"], "분류": v["category"],
            "초보적합": v["beginner_friendly"], "초보사유": v["beginner_reason"],
        },
        "재배환경": {
            "생육적온": v["growth_temp"], "비대적온": v["bulking_temp"], "토양산도": v["soil_ph"],
            "토양": v["soil_type"], "주의토양": v["caution_soil"],
            "권장작형": v["recommended_season_text"], "비권장작형": v["not_recommended_season_text"],
            "지역메모": v["regional_notes"],
        },
        "재배방법": {"씨감자관리": v["seed_potato"], "공통일정": common.get("pre_planting")},
        "생육관리": {"관리": v["cultivation_management"], "공통관리": common.get("tuber_bulking_stage")},
        # 역병은 별도 자료(data/late_blight.csv)에 위험 등급·증상·대처가 있다. 품종의
        # disease_and_pest_risks 에 역병이 없는 품종도 많아서(추백은 감자바이러스Y만 기재)
        # 그것만 보면 "역병 자료가 없다"는 사실 자체를 알려줄 수 없다.
        "주의점": {"핵심주의": v["key_warnings"], "병해충": v["diseases"],
                 "생리장해": v["disorders"], "역병": blight_data.blight_info(crop, v["name"])},
        "수확": {"수확": v["harvest"], "공통수확": common.get("maturity_and_harvest")},
        "저장판매": {"저장·판매": v["storage"]},
        "선택기준": {"이럴때선택": v["selection_conditions"]},
    }
    if topic and topic in sections:
        body = {topic: sections[topic]}
    else:
        body = sections

    return {
        "crop": crop, "cultivar": v["name"], "aliases": v["aliases"],
        "sections": body, "report": v["report"],
        "cautions": cultivar_data.dataset_cautions(crop),
    }
