# -*- coding: utf-8 -*-
"""과수(사과·배) 품종 적합도 채점 — 수확기를 앵커로 쓰는 모델.

왜 감자 모델(cultivar_fit)을 그대로 못 쓰나
  감자 모델은 "파종일을 5일 간격으로 훑어 → 생육일수만큼 자라 → 서리 전에 수확"이다.
  과수는 다년생이라 **파종일이 없다.** growth_period_days 도 '만개후일수'여서 무상기간과
  비교할 대상이 아니다(그렇게 짰다가 후지가 평창에서 '재배 불가'로 찍혔다 - breed.md §17).

  대신 과수 데이터가 확실히 갖고 있는 것은 **품종별 수확기**다
  (maturity.harvest_period: 후지 '10월 하순~11월 상순', 홍로 '9월 상·중순').
  그래서 파종일 자리에 **수확기**를 놓고, 그 구간과 그 앞 구간(착색기)의 기상으로 채점한다.
  품종마다 수확기가 다르므로 같은 지역에서도 점수가 실제로 갈린다
  (청송 착색기 일평균: 후지 15.4℃ / 홍로 25.1℃).

왜 상추·오이는 이 방식으로도 안 되나 (실측)
  품종별로 다른 기후 수치가 있어야 순위가 생긴다. 상추 청치마·적축면은 생육일수(45~65)·
  적온(15~20)·고온플래그가 **완전히 같고**, 오이 3품종군도 전부 같다(적온 20~25, 생육일수
  없음). 무엇을 넣어도 동점이라 순위에 이유를 붙일 수 없다. 그 두 작물은 조건 매칭
  (cultivar_conditions)에 그대로 둔다.

기상은 전부 평년이다
  season_window.station_climatology 가 '최근 10개 완결된 연도'만 쓴다(올해·예보 제외).
  구간 통계는 연도별로 계산한 뒤 평균한다 - 감자 모델과 같은 방식이다(§9-2).

항목과 가중치 (합 100)
  착색기주간 30 · 착색기야간 15 · 수확기강수 20 · 수확기고온 20 · 서리여유 10 · 토양 5
  · 착색기 두 항목은 **사과만** 해당한다(배는 착색 적온 수치가 데이터에 없다). 배에서는
    빼고 나머지를 재정규화한다 - 없는 값에 100점을 주면 배가 공짜 점수를 받는다.
  · 서리여유는 감점 항목이지 하드 게이트가 아니다. 첫서리(일최저 0℃)는 과실을 죽이지
    않는다(성목 내한성 -30℃, 사과 flower_frost_threshold_by_stage 는 봄 개화기 기준).
  · 착색기 두 항목은 적온보다 **높을 때만** 깎는다. 착색은 서늘해야 잘 되므로 낮은 것은
    흠이 아니고, 아래로도 깎으면 서늘한 산지가 부당하게 손해를 본다.
"""

import logging

import cultivar_data
import season_window
from scoring_engine import _binary_range_score  # noqa: F401  (기존 점수 함수 재사용 규약)

logger = logging.getLogger(__name__)

FRUIT_CROPS = ("사과", "배")

WEIGHTS = {
    "착색기주간": 30,
    "착색기야간": 15,
    "수확기강수": 20,
    "수확기고온": 20,
    "서리여유": 10,
    "토양": 5,
}
# ⚠️ '성숙기온도'(작물표준 maturation_optimal 20~25℃)를 처음에 넣었다가 걷어냈다.
#    그 값은 **여름~초가을 과실이 커지는 시기** 기준이지 수확 시점 기온이 아니다. 후지
#    수확기(10월 하순~11월 상순) 평균 9.2℃를 20~25℃와 비교해 30점 바닥이 나왔는데,
#    같은 자료가 착색 적온을 12~13℃로 주고 있어 서로 모순이다. 대신 데이터가 정량으로
#    준 착색 적온 두 개(주간 일평균 12~13℃, 야간 8℃)를 축으로 쓴다.

# 착색기 = 수확 시작 직전 N일. 원문이 '착색기'를 일수로 주지 않아 관행값을 쓰고,
# 화면 문구에 '수확 전 30일'임을 그대로 드러낸다(사용자가 근거를 확인할 수 있게).
COLORING_DAYS = 30
# 집중강수일 기준은 season_window 와 같은 값을 쓴다(50mm/일).
HEAVY_RAIN_MM = season_window.HEAVY_RAIN_MM


def _piecewise_range(value, lo, hi, span, floor):
    """범위 안이면 100점, 벗어난 정도만큼 floor까지 부드럽게 깎는다(§8-5 방식).

    span℃ 만큼 벗어났을 때 (100+floor)/2 근처가 되도록 기울기를 잡는다.
    """
    if value is None or lo is None:
        return None
    if lo <= value <= hi:
        return 100.0
    dist = (lo - value) if value < lo else (value - hi)
    return max(float(floor), 100.0 - (dist / float(span)) * (100.0 - floor))


def _mean_over(clim, start_mmdd, end_mmdd, field):
    """[start, end] 구간 일자료 field 의 연도평균. 연도별로 먼저 평균한 뒤 평균한다."""
    per_year = []
    for _y, records in (clim.get("by_year") or {}).items():
        vals = [r[field] for r in records
                if r.get(field) is not None
                and start_mmdd <= r["date"][4:6] + "-" + r["date"][6:8] <= end_mmdd]
        if vals:
            per_year.append(sum(vals) / len(vals))
    return (sum(per_year) / len(per_year)) if per_year else None


def _sum_and_days_over(clim, start_mmdd, end_mmdd):
    """구간 강수합·집중강수일수·고온일수(최고 30℃ 초과)의 연도평균."""
    rain, heavy, hot = [], [], []
    for _y, records in (clim.get("by_year") or {}).items():
        rows = [r for r in records
                if start_mmdd <= r["date"][4:6] + "-" + r["date"][6:8] <= end_mmdd]
        if not rows:
            continue
        rain.append(sum(r.get("sumRn") or 0.0 for r in rows))
        heavy.append(sum(1 for r in rows if (r.get("sumRn") or 0.0) >= HEAVY_RAIN_MM))
        hot.append(sum(1 for r in rows if (r.get("maxTa") or -99) > 30.0))
    avg = lambda xs: (sum(xs) / len(xs)) if xs else None      # noqa: E731
    return avg(rain), avg(heavy), avg(hot)


# ═══════════════════════════════════════════════════════════════
# 항목별 점수
# ═══════════════════════════════════════════════════════════════

def score_coloring(mean_temp, lo, hi):
    """착색기 기온. 사과만 해당(배는 기준값이 데이터에 없다).

    적온보다 **높을 때만** 깎는다. 착색은 서늘해야 잘 되므로 적온보다 낮은 것은 흠이
    아니다(오히려 유리하다) - 아래쪽으로 깎으면 서늘한 산지가 부당하게 손해를 본다.
    """
    if mean_temp is None or lo is None:
        return None, {}
    if mean_temp <= hi:
        score = 100.0
    else:
        score = max(20.0, 100.0 - ((mean_temp - hi) / 6.0) * 80.0)
    return score, {"착색기평균": round(mean_temp, 1), "적온": f"{lo}~{hi}℃",
                   "설명": "착색은 서늘할수록 유리해 적온보다 낮은 것은 감점하지 않아요"}


def score_coloring_night(min_temp_mean, target):
    """착색기 야간 기온. 데이터가 '야간 평균 8℃ 전후'를 정량으로 준다.

    주간과 마찬가지로 **높을 때만** 깎는다. 착색기(9~11월)에 야간이 낮은 것은 일교차를
    키워 안토시아닌 합성에 유리하고, 이 시기에 동해가 오는 온도는 아니다.
    """
    if min_temp_mean is None or target is None:
        return None, {}
    if min_temp_mean <= target:
        score = 100.0
    else:
        score = max(20.0, 100.0 - ((min_temp_mean - target) / 8.0) * 80.0)
    return score, {"착색기야간평균": round(min_temp_mean, 1), "적온": f"{target}℃ 전후",
                   "설명": "야간이 서늘하면 일교차가 커져 착색에 유리해요"}


def score_harvest_rain(rain_mm, heavy_days, window_days):
    """수확기 강수: 총량이 많을수록·집중강수일이 많을수록 감점.

    §8-5의 강수 원칙(부족·과습 중 나쁜 쪽)을 과수 수확기에 맞게 바꿨다. 수확기에는
    '부족'이 문제가 아니라 **비가 오는 것 자체**가 문제다(열과·낙과·당도 저하·수확 지연).
    그래서 부족 쪽은 보지 않고 과다만 본다.
    """
    if rain_mm is None:
        return None, {}
    # 수확기 하루 평균 강수 3mm를 기준으로 잡는다(30일 구간이면 90mm).
    per_day = rain_mm / window_days if window_days else 0.0
    base = _piecewise_range(per_day, 0.0, 3.0, span=3.0, floor=30.0)
    penalty = max(0.0, (heavy_days or 0) - 1) * 12.0
    return max(0.0, min(base, 100.0 - penalty)), {
        "수확기강수mm": round(rain_mm), "하루평균mm": round(per_day, 1),
        "집중강수일수": round(heavy_days, 1) if heavy_days is not None else None,
        "과습감점": round(penalty, 1),
    }


def score_harvest_heat(hot_days, threshold):
    """수확기 고온일수(최고 30℃ 초과). 성숙·착색이 지연되고 일소가 늘어난다."""
    if hot_days is None:
        return None, {}
    penalty = min(hot_days * 6.0, 70.0)
    return max(30.0, 100.0 - penalty), {
        "30℃초과일수": round(hot_days, 1), "기준": f"{threshold}℃ 이상에서 성숙 지연",
        "감점": round(penalty, 1),
    }


def score_frost_slack(harvest_end, first_fall):
    """수확 종료와 첫서리 사이 여유. 하드 게이트가 아니라 감점이다.

    첫서리는 일최저 0℃ 첫날일 뿐이고 성목 내한성은 훨씬 아래다. 다만 수확이 서리 뒤로
    밀리면 늦게 따는 몫이 서리·저장성 위험을 안으므로 여유가 없을수록 깎는다.
    """
    if not harvest_end or not first_fall:
        return None, {}
    # ⚠️ mmdd_diff(a, b) 는 a - b 다. 인자를 뒤집어 쓰면 부호가 반대가 되어, 수확이
    #    첫서리보다 21일 늦은 후지가 '여유 21일'로 100점을 받는다(실제로 그랬다).
    slack = season_window.mmdd_diff(first_fall, harvest_end)   # 첫서리 - 수확종료
    if slack >= 14:
        score = 100.0
    elif slack >= 0:
        score = 70.0 + (slack / 14.0) * 30.0
    else:
        score = max(30.0, 70.0 + (slack / 21.0) * 40.0)
    return score, {"수확종료": harvest_end, "첫서리": first_fall, "여유일": slack}


def score_soil_ph(readings, lo, hi):
    """토양 산도만 본다. 과수는 유기물·인산 기준을 품종 자료가 갖고 있지 않다."""
    ph = (readings or {}).get("pH")
    s = _piecewise_range(ph, lo, hi, span=1.0, floor=40.0)
    if s is None:
        return None, {}
    return s, {"pH": round(ph, 2), "적정": f"{lo}~{hi}"}


# ═══════════════════════════════════════════════════════════════
# 종합
# ═══════════════════════════════════════════════════════════════

def _grade_of(score):
    """For_Frontend.md §3과 같은 4단계(화면 라벨을 통일하기 위해)."""
    if score is None:
        return None, "산출 불가"
    if score >= 80:
        return "good", "우수"
    if score >= 60:
        return "normal", "양호"
    if score >= 40:
        return "caution", "주의"
    return "bad", "위험"


def _combine(items):
    """None인 항목은 빼고 가중치를 재정규화한다(§10의 원칙 - 없는 값에 100점을 주지 않는다).

    반환 (총점, breakdown, 제외항목)
    """
    used = {k: v for k, v in items.items() if v[0] is not None}
    excluded = [k for k in items if k not in used]
    total_w = sum(WEIGHTS[k] for k in used) or 1
    total = sum(v[0] * WEIGHTS[k] for k, v in used.items()) / total_w
    breakdown = {k: {"점수": round(v[0], 1), "가중치": round(WEIGHTS[k] * 100 / total_w, 1),
                     "근거": v[1]}
                 for k, v in used.items()}
    return round(total, 1), breakdown, excluded


def score_fruit_cultivars(region_name, crop, experience="beginner",
                          years=season_window.DEFAULT_YEARS, allow_fetch=True,
                          climatology=None, soil_readings=None, station=None,
                          matched_region=None, harvest_window_of=None):
    """사과·배 품종 순위. 반환은 cultivar_fit.score_cultivars 와 같은 계약을 지킨다.

    harvest_window_of(variety) -> (시작mmdd, 끝mmdd) 를 주입할 수 있다(테스트·재사용용).
    기본값은 cultivar_conditions.harvest_window 다 - 파싱 규칙을 두 곳에 두지 않는다.
    """
    if crop not in FRUIT_CROPS:
        return {"error": f"이 모델은 사과·배만 다뤄요: {crop}"}

    payload = cultivar_data.load_crop(crop)
    if not payload:
        return {"error": f"품종 데이터가 아직 없어요: {crop}",
                "available_crops": cultivar_data.available_crops()}
    varieties = payload["varieties"]

    if harvest_window_of is None:
        import cultivar_conditions
        harvest_window_of = cultivar_conditions.harvest_window

    # ── 지역 → 관측소 ──
    distance_km = None
    if station is None:
        from region_mapper import find_nearest_station
        m = find_nearest_station(region_name)
        if m.get("status") != "matched":
            return {"status": m.get("status", "not_found"), "region": region_name, "crop": crop,
                    "error": f"지역을 찾지 못했어요: {region_name} ({m.get('status')})"}
        station = m["station"]
        matched_region = m.get("matched_region") or {}
        distance_km = m.get("distance_km")
    matched_region = matched_region or {}

    region = {
        "station_id": station["station_id"], "station_name": station.get("station_name"),
        "cluster_id": station.get("cluster_id"), "cluster_name": station.get("cluster_name"),
        "distance_km": distance_km, "sigungu_name": matched_region.get("sigungu_name"),
    }

    # ── 평년 기상 (최근 10개 완결 연도) ──
    if climatology is None:
        climatology = season_window.station_climatology(
            station["station_id"], years=years, allow_fetch=allow_fetch)
    if not climatology or climatology.get("status") != "ok":
        return {"status": "no_climate", "region": region_name, "crop": crop,
                "region_metrics": region,
                "error": (climatology or {}).get("message")
                         or "이 지역의 과거 기상자료(ASOS)를 확보하지 못해 품종 판정을 할 수 없어요"}
    frost = climatology.get("frost") or {}
    region.update({
        "frost_free_days": frost.get("frost_free_days"),
        "last_spring_frost": frost.get("last_spring"),
        "first_fall_frost": frost.get("first_fall"),
        "years_used": frost.get("years_used"),
        "frost_free_note": frost.get("frost_free_note"),
    })

    # ── 토양 (있으면 반영, 없으면 항목 제외) ──
    soil_note = None
    if soil_readings is None:
        try:
            import soil
            soil_readings = (soil.get_soil_readings(region["sigungu_name"], crop)
                             if region["sigungu_name"] else {})
        except Exception as e:                                    # noqa: BLE001
            logger.error("[cultivar_fruit_fit] 흙토람 조회 실패: %s", e)
            soil_readings, soil_note = {}, f"토양 조회 실패({e})"

    std = cultivar_data._crop_standards(crop)                     # noqa: SLF001 (같은 패키지)
    std_temp = std.get("temperature") or {}
    std_ph = (std.get("soil") or {}).get("ph") or {}
    common_env = payload.get("common_environment") or {}
    col_lo, col_hi = (common_env.get("coloring_daily_mean_c") or {}).get("min"), \
                     (common_env.get("coloring_daily_mean_c") or {}).get("max")
    col_night_target = common_env.get("coloring_night_mean_c")
    hot_threshold = (std_temp.get("high_temp_risk") or {}).get("threshold") or 30

    ranking, skipped = [], []
    for v in varieties:
        win = harvest_window_of(v)
        if not win:
            skipped.append({"cultivar": v["name"],
                            "reason": "이 품종의 수확기 자료가 없어 기상으로 판정할 수 없어요"})
            continue
        h_start, h_end = win
        c_start = season_window.mmdd_add(h_start, -COLORING_DAYS)
        # ⚠️ mmdd_diff(a, b) = a - b. (h_start, h_end) 순으로 주면 음수가 되어 max(1,…)에
        #    걸려 window_days=1 이 되고, 수확기 강수 하루평균이 구간 길이만큼 과대해진다
        #    (17mm 구간이 16.5mm/일로 나왔다).
        window_days = max(1, season_window.mmdd_diff(h_end, h_start) + 1)

        col_mean = _mean_over(climatology, c_start, h_start, "avgTa")
        col_night = _mean_over(climatology, c_start, h_start, "minTa")
        rain_mm, heavy_days, hot_days = _sum_and_days_over(climatology, h_start, h_end)

        items = {
            "착색기주간": score_coloring(col_mean, col_lo, col_hi),
            "착색기야간": score_coloring_night(col_night, col_night_target),
            "수확기강수": score_harvest_rain(rain_mm, heavy_days, window_days),
            "수확기고온": score_harvest_heat(hot_days, hot_threshold),
            "서리여유": score_frost_slack(h_end, frost.get("first_fall")),
            "토양": score_soil_ph(soil_readings, std_ph.get("optimal_min"), std_ph.get("optimal_max")),
        }
        total, breakdown, excluded = _combine(items)
        grade, grade_label = _grade_of(total)

        cautions, badges = [], []
        # 착색 적온(12~13℃)은 사실상 **만생종 기준**이다. 데이터에 숙기별 착색 기준이
        # 없어서 조생종은 착색기가 7~8월(25℃+)이라 구조적으로 바닥 점수를 받는다.
        # 조기 출하가 목적인 품종을 점수만 보고 배제하지 않도록 그 사실을 밝힌다
        # (없는 숙기별 기준을 만들어 점수를 보정하지는 않는다 - §10 원칙).
        if v.get("early_market_preferred"):
            badges.append("조기 출하용")
            cautions.append("이 점수는 착색을 크게 보는데, 착색 적온(12~13℃)은 만생종 기준이라 "
                            "조기 출하용 품종은 낮게 나와요. 이 품종의 목적은 색보다 이른 출하예요")
        bloom = v.get("bloom_to_harvest") or {}
        conf = bloom.get("confidence")
        if conf and not str(conf).startswith(("확실", "보통")):
            cautions.append(f"수확기 추정에 쓰인 만개후일수({bloom.get('min')}~{bloom.get('max')}일)가 "
                            f"추정치예요({conf})")
        if excluded:
            cautions.append("자료가 없어 " + "·".join(excluded) + " 항목을 빼고 계산했어요")

        ranking.append({
            "cultivar": v["name"], "aliases": v["aliases"], "maturity": v["maturity"],
            "category": v["category"],
            "score": total, "grade": grade, "grade_label": grade_label,
            "cultivation_type": "",                    # 과수는 작형이 없다
            "planting_window": {},                     # 파종이 없다
            "harvest_window": {"from": h_start, "to": h_end},
            "coloring_window": {"from": c_start, "to": h_start},
            "breakdown": breakdown,
            "excluded_items": excluded,
            "blockers": [],
            "cautions": cautions,
            "variety_warnings": (v.get("key_warnings") or [])[:3],
            "badges": badges,
            "beginner_friendly": v.get("beginner_friendly"),
            "beginner_reason": v.get("beginner_reason"),
            "primary_use": v.get("primary_use"),
            "headline": v.get("headline"),
            "bloom_to_harvest": bloom or None,
            "report": v.get("report"),
        })

    ranking.sort(key=lambda r: -r["score"])

    region_cautions = list(cultivar_data.dataset_cautions(crop))
    if distance_km and distance_km >= 10:
        region_cautions.insert(0, (
            f"기준 관측소({region['station_name']})가 {distance_km}km 떨어져 있어요. 과원이 더 높은 "
            f"곳이면 기온이 낮고 서리가 이르니 실제 밭 기준으로 조정하세요"))
    if crop == "사과":
        region_cautions.insert(0, "점수는 착색·수확기 기상 중심이에요. 착색 적온(12~13℃)이 "
                                  "만생종 기준이라 조생종은 낮게 나오니, 이른 출하가 목적이면 "
                                  "점수보다 수확기와 판매 계획을 먼저 보세요")
    if crop == "배":
        region_cautions.insert(0, "배는 착색 적온 수치가 자료에 없어 착색기 두 항목을 빼고 계산했어요")

    return {
        "status": "matched",
        "crop": crop, "region": region_name, "experience": experience,
        "scoring_mode": cultivar_data.SCORING_CLIMATE_FRUIT,
        "unit": payload["unit"],
        "region_metrics": region,
        "soil_readings": {k: (round(x, 2) if isinstance(x, (int, float)) else x)
                          for k, x in (soil_readings or {}).items()},
        "ranking": ranking, "skipped": skipped,
        "reliability": "정상" if (soil_readings or {}).get("pH") else "주의",
        "reliability_reason": (soil_note or ("토양 pH 결측"
                                            if not (soil_readings or {}).get("pH") else None)),
        "cautions": region_cautions,
        "data_sources": {
            "기상": f"기상청 ASOS 일자료 {frost.get('years_used')}년 평년"
                    f"(관측소 {region['station_name']}) — 올해·예보 미사용",
            "토양": "흙토람 SoilExamStat V2" + (f" — {soil_note}" if soil_note else ""),
            "품종": payload.get("source_file"),
            "수확기": "품종 자료의 maturity.harvest_period",
        },
    }
