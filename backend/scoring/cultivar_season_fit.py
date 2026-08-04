# -*- coding: utf-8 -*-
"""상추·오이 **작형(재배형) 적합도** 점수 — breed.md §23.

■ 왜 품종이 아니라 작형을 줄 세우는가
  상추 3품종(청치마·적축면·로메인)과 오이 3품종군(다다기·취청·가시)은 품종 데이터에
  기후 수치(env)가 **하나도** 없다. 그래서 기존 조건 모드(cultivar_conditions)로 채점하면
  같은 작물의 품종이 전부 동점이 나온다 - 순위처럼 보이지만 아무것도 가르지 않는다.
  반면 작형은 파종기가 두 달씩 다르므로 작기 기상이 실제로 갈린다. 정선(평창) 상추를
  재보면 작기 평균기온이 고랭지재배 16.6℃ / 여름재배 18.9℃ / 가을재배 21.4℃로 벌어지고,
  이 차이가 곧 추대 위험의 차이다. 그래서 이 모듈은 **작형을 채점한다**.

■ 작기 창은 '한 포기의 작기'다
  처음에는 창을 파종기 시작~수확기 끝(151~212일)으로 잡았다가 걷어냈다. 그건 한 포기가
  사는 기간이 아니라 그 작형에서 가능한 **모든** 파종과 수확을 뭉갠 구간이어서, 상추
  1월 파종 창의 평균기온이 1월과 5월의 혼합(5.9℃)으로 나왔다. 창은 파종일 + 생육일수다.
  생육일수는 CROP_SCHEDULE의 (수확기 시작 - 파종기 시작)이 원본이고, 파종일은 감자와
  같은 방식으로 파종기 안을 훑어 가장 좋은 날을 고른다.

■ 추대 위험을 '누적'으로 만들지 않은 이유
  작물표준의 상추 bolting_risk는 "25℃에서 파종 10일 만에 추대, 20℃ 20일, 15℃ 30일,
  15℃ 이하에서는 추대 크게 지연"이라는 **정성 서술에 붙은 예시 수치**다. 이것을 일별
  진행률(1/소요일수)로 누적하는 모델을 만들어 재봤더니 노지 3작형이 전부 2.6~3.0
  (= 한 작기에 추대가 세 번)으로 나왔다. 노지 여름·가을 상추는 실재하는 관행 재배이므로
  그 결론은 틀렸다. 원문이 지지하는 것은 **작기 평균기온이 높을수록 추대가 빨라진다**는
  단조 관계와 15/20/25℃라는 눈금뿐이다. 그 눈금 위에서만 점수를 매긴다.

■ 시설 작형을 노지 기상으로 채점하는 것의 의미
  상추 시설 봄재배·겨울재배, 오이 촉성·반촉성재배는 하우스 안에서 키운다. 우리는 하우스
  **안** 기온을 알 수 없다. 그래서 이 점수는 '재배 적합도'가 아니라 **노지 기상이 작물
  적온에 얼마나 맞는가**로 정의하고, 그 정의를 화면 문구에 그대로 쓴다. 노지 작형에서는
  이 값이 곧 재배 조건이고, 시설 작형에서는 **시설이 메워야 하는 몫**(난방·보온 부담)이다.
  같은 숫자, 같은 정의, 두 가지 읽기. 시설 작형에는 난방도일을 함께 계산해 "0점"으로
  보이는 것을 실제 부담 수치로 바꿔 준다.
  강수 축은 시설 작형에서 **제외하고 가중치를 재정규화**한다 - 하우스 안에는 비가 오지
  않으므로 노지 강수로 판정할 수 없다(§10의 원칙: 없는 값에 점수를 주지 않는다).

기온·강수는 모두 평년(최근 10개 완결 연도 ASOS 일자료)이다. 올해·예보는 쓰지 않는다.
"""

import logging

import cultivar_data
import cultivar_fit
import season_window
from chat_schedule import CROP_SCHEDULE

logger = logging.getLogger(__name__)

SEASON_CROPS = ("상추", "오이")

# 재배 구조. CROP_SCHEDULE[작물].seasons[].region 문구가 원본이다.
STRUCT_OPEN = "노지"
STRUCT_FACILITY = "시설"
STRUCT_BOTH = "노지·시설 겸용"

WEIGHTS = {
    # 상추 - 호냉성. 실패는 (1) 적온을 벗어난 생육 (2) 고온 추대 (3) 서리 (4) 과습 부패로 온다.
    "상추": {"생육적온": 35, "추대위험": 25, "서리저온": 20, "작기강수": 20},
    # 오이 - 고온성. 낮·밤 온도를 따로 관리하는 작물이라 평균만으로는 부족하다.
    "오이": {"생육적온": 30, "주간적온": 20, "야간적온": 15,
             "고온장해": 15, "저온장해": 10, "작기강수": 10},
}

# 파종기를 훑는 간격(일). 감자(cultivar_fit.SCAN_STEP_DAYS)와 같은 값을 쓴다.
SCAN_STEP_DAYS = 5
HEAVY_RAIN_MM = season_window.HEAVY_RAIN_MM

# 오이 수확 후반의 추위를 경고할 기준. 작물표준 오이 growth_suppression_threshold
# ("10~12℃ 이하에서 생육 크게 억제")의 하한을 쓴다. 생육 스트레스 하한(15℃)을 쓰면
# 6월 수확기의 밤 최저 14℃에도 보온 경고가 붙는다.
GROWTH_SUPPRESSION_C = 10.0

# 1·2위 점수 차가 이보다 작으면 '사실상 비슷하다'고 밝힌다. 정선 상추에서 여름재배
# 94.2 / 고랭지재배 94.1이 나왔는데(두 작형의 파종기가 4/30-5/1로 맞닿아 파종일 탐색이
# 거의 같은 창을 고른다), 0.1점 차에 1위·2위 딱지를 붙이면 없는 우열을 만든다.
CLOSE_SCORE_GAP = 3.0

# 작형 이름 -> reference_data.PRECIP_THRESHOLDS 의 작형 키. 강수 기준값이 정의된 작형
# 이름과 화면 작형 이름이 다르므로(상추 '저지대재배' vs '여름재배') 명시적으로 잇는다.
# cultivar_fit.score_rain의 fallback(첫 키 자동 선택)에 맡기면 오이 노지 조숙재배가
# 시설 촉성재배 기준(적정 73.9mm)으로 판정된다.
RAIN_REF_SEASON = {
    "상추": {"고랭지재배": "고랭지재배", "여름재배": "저지대재배", "가을재배": "저지대재배",
             "시설 봄재배": "저지대재배", "겨울재배": "저지대재배"},
    "오이": {"촉성재배": "촉성재배", "반촉성재배": "반촉성재배",
             "조숙재배": "반촉성재배", "억제재배": "반촉성재배"},
}

# 작형별로 자료가 권하는 품종. 오이는 품종 데이터의 selection_guide가 조건→품종군을
# 명시하므로 그 조건 문구를 그대로 근거로 달아 잇는다. 상추는 selection_guide가 비어
# 있어(자료에 작형별 품종 구분이 없다) 매핑하지 않고 그 사실을 화면에 밝힌다.
CUCUMBER_SEASON_GUIDE = {
    "촉성재배":   ["겨울철 저온·약광 시설재배"],
    "반촉성재배": ["겨울철 저온·약광 시설재배", "봄철 반촉성·조숙 및 국내 생식용 시장"],
    "조숙재배":   ["봄철 반촉성·조숙 및 국내 생식용 시장", "여름철 노지·터널조숙재배"],
    "억제재배":   ["여름철 노지·터널조숙재배"],
}


# ═══════════════════════════════════════════════════════════════
# 1. 작형 사양 (CROP_SCHEDULE 파생)
# ═══════════════════════════════════════════════════════════════

def _structure(region_text):
    """작형의 재배 구조. '전국 평지·시설'처럼 둘 다 적힌 작형은 겸용이다.

    부분문자열로 '시설'만 찾으면 오이 억제재배('전국 평지·시설')가 시설로 분류되어
    오이 노지 작형이 조숙재배 하나만 남고 순위가 성립하지 않는다.
    """
    t = region_text or ""
    facility = ("시설" in t) or ("하우스" in t)
    open_field = ("평지" in t) or ("고랭지" in t) or ("노지" in t)
    if facility and open_field:
        return STRUCT_BOTH
    return STRUCT_FACILITY if facility else STRUCT_OPEN


def _parse_period(text):
    """'5월 상순~중순 파종 · 7월 상순~8월 상순 수확' -> (파종창, 수확창).

    날짜 해석은 cultivar_conditions.parse_period를 그대로 쓴다(상순·중순·하순 규칙이
    이미 거기 있고, 두 곳에서 다르게 읽으면 화면과 챗봇 날짜가 갈린다).
    """
    import cultivar_conditions
    sow = harvest = None
    for part in [p.strip() for p in (text or "").split("·")]:
        window = cultivar_conditions.parse_period(part)
        if not window:
            continue
        if "수확" in part:
            harvest = window
        elif any(k in part for k in ("파종", "정식", "심")):
            sow = window
    return sow, harvest


def season_specs(crop):
    """작물의 작형 사양 목록. 읽을 수 없는 작형은 담지 않는다(날짜를 추측하지 않는다)."""
    out = []
    for s in (CROP_SCHEDULE.get(crop) or {}).get("seasons") or []:
        sow, harvest = _parse_period(s.get("period"))
        if not (sow and harvest):
            logger.warning("작형 기간 해석 실패: %s %s (%s)", crop, s.get("name"), s.get("period"))
            continue
        days = season_window.mmdd_diff(harvest[0], sow[0])
        if days <= 0:                                   # 해를 넘기는 작형(겨울재배 등)
            days += 365
        out.append({
            "name": s.get("name"),
            "sow_from": sow[0], "sow_to": sow[1],
            "harvest_from": harvest[0], "harvest_to": harvest[1],
            "days": days,
            "structure": _structure(s.get("region")),
            "region_text": s.get("region") or "",
            "period_text": s.get("period") or "",
        })
    return out


# ═══════════════════════════════════════════════════════════════
# 2. 작기 구간 기상 (해를 넘겨도 잇는다)
# ═══════════════════════════════════════════════════════════════

def _date_index(clim, year):
    """year와 year+1의 일자료를 date -> 레코드로. 작기가 해를 넘기므로 두 해를 함께 본다."""
    from datetime import date as _date
    idx = {}
    for y in (year, year + 1):
        for r in (clim.get("by_year") or {}).get(y) or []:
            s = str(r.get("date") or "")
            if len(s) == 8:
                try:
                    idx[_date(int(s[:4]), int(s[4:6]), int(s[6:8]))] = r
                except ValueError:
                    continue
    return idx


def _mmdd_in_range(mmdd, start, end):
    """start~end(둘 다 포함) 안인가. end < start면 해를 넘기는 구간으로 본다."""
    if start <= end:
        return start <= mmdd <= end
    return mmdd >= start or mmdd <= end


def mean_in_mmdd_range(clim, start_mmdd, end_mmdd, field):
    """MM-DD 구간의 field 평년 평균. 연도 짝을 맞추지 않고 날짜만 걸러 평균한다
    (수확기처럼 '그 무렵의 기온'만 알면 되는 구간에 쓴다)."""
    vals = []
    for _y, records in (clim.get("by_year") or {}).items():
        for r in records:
            s = str(r.get("date") or "")
            if len(s) != 8 or r.get(field) is None:
                continue
            if _mmdd_in_range(s[4:6] + "-" + s[6:8], start_mmdd, end_mmdd):
                vals.append(r[field])
    return (sum(vals) / len(vals)) if vals else None


def cycle_metrics(clim, plant_mmdd, days, low_stress_c=None, hot_c=30.0, hot_limit_c=35.0,
                  heat_base_c=None):
    """파종일 + 생육일수 구간의 평년 기상. 연도별로 낸 뒤 연도 평균을 준다.

    season_window.window_metrics를 쓰지 않는다 - 그 함수는 수확일이 12/31을 넘으면
    12월 31일로 잘라 버려서, 오이 촉성재배(10월 파종~이듬해 4월 수확)와 상추 겨울재배가
    작기의 절반만 채점된다.
    """
    from datetime import date as _date, timedelta

    if not clim or clim.get("status") != "ok" or not days:
        return None
    month, day = int(plant_mmdd[:2]), int(plant_mmdd[3:5])
    need = int(days)
    acc, years_used = {}, 0

    def add(key, value):
        acc.setdefault(key, []).append(value)

    for year in sorted((clim.get("by_year") or {})):
        idx = _date_index(clim, year)
        try:
            start = _date(year, month, day)
        except ValueError:                                  # 2/29 방어
            continue
        span = [idx[start + timedelta(days=i)] for i in range(need + 1)
                if (start + timedelta(days=i)) in idx]
        if len(span) < need * 0.9:                          # 이듬해 자료가 없는 마지막 해 등
            continue
        tavg = [r["avgTa"] for r in span if r.get("avgTa") is not None]
        tmax = [r["maxTa"] for r in span if r.get("maxTa") is not None]
        tmin = [r["minTa"] for r in span if r.get("minTa") is not None]
        if not (tavg and tmax and tmin):
            continue
        years_used += 1
        add("tavg", sum(tavg) / len(tavg))
        add("tmax", sum(tmax) / len(tmax))
        add("tmin", sum(tmin) / len(tmin))
        add("rain_mm", sum(r.get("sumRn") or 0.0 for r in span))
        add("heavy_days", sum(1 for r in span if (r.get("sumRn") or 0.0) >= HEAVY_RAIN_MM))
        add("frost_days", sum(1 for t in tmin if t <= season_window.FROST_TEMP_C))
        add("hot_days", sum(1 for t in tmax if t > hot_c))
        add("hot_limit_days", sum(1 for t in tmax if t > hot_limit_c))
        if low_stress_c is not None:
            add("cold_days", sum(1 for t in tmin if t < low_stress_c))
        if heat_base_c is not None:
            # 난방도일: 일평균기온이 적온 하한보다 낮은 만큼을 더한다(℃·일).
            add("heating_degree", sum(max(0.0, heat_base_c - t) for t in tavg))
        add("days_used", len(span))

    if not years_used:
        return None
    out = {k: sum(v) / len(v) for k, v in acc.items()}
    out["years_used"] = years_used
    out["plant"] = plant_mmdd
    out["harvest"] = season_window.mmdd_add(plant_mmdd, need)
    out["days"] = need
    return out


# ═══════════════════════════════════════════════════════════════
# 3. 항목별 점수
# ═══════════════════════════════════════════════════════════════

# 적온에서 벗어난 거리(℃) -> 점수. 범위 근처는 급하게, 멀어지면 완만하게 떨어진다.
#
# ⚠️ 처음에는 '(거리/span)만큼 선형으로 깎고 floor에서 멈춤'으로 만들었다가 걷어냈다.
#    오이 겨울 작형은 작기 평균기온이 적온(20~25℃)에서 15~22℃ 떨어져 세 온도 축이 전부
#    floor에 붙었고, 그 결과 촉성재배와 반촉성재배가 **모든 지역에서 똑같이 27.5점**이
#    나왔다. 동점이 문제인 것보다, 파종기를 훑는 탐색까지 무력해져(전부 동점이라 첫 후보가
#    이김) 반촉성재배 권장 파종일이 가장 추운 12월 1일로 찍힌 것이 더 큰 결함이었다.
#    멀리서도 기울기가 남아 있어야 순위와 탐색이 산다.
_TEMP_DISTANCE_POINTS = [
    (0.0, 100.0), (2.0, 85.0), (4.0, 65.0), (7.0, 40.0),
    (11.0, 20.0), (16.0, 5.0), (22.0, 0.0),
]


def score_optimal_temp(mean_temp, lo, hi, label, scale=1.0):
    """작기 대표기온이 적온 범위에 있는가. 벗어난 거리로 채점한다.

    scale은 축마다 허용 폭이 다른 것을 반영한다(야간 최저기온은 낮 기온보다 넓게 본다).
    """
    if mean_temp is None or lo is None or hi is None:
        return None, {}
    if lo <= mean_temp <= hi:
        dist = 0.0
    else:
        dist = (lo - mean_temp) if mean_temp < lo else (mean_temp - hi)
    score = cultivar_fit._piecewise(dist / float(scale), _TEMP_DISTANCE_POINTS)
    return score, {"작기평균기온": round(mean_temp, 1), "적온": f"{lo}~{hi}℃",
                   "적온과의차": round(dist, 1), "기준": label}


def score_bolting(mean_temp):
    """상추 추대 위험. 작물표준 bolting_risk의 15/20/25℃ 눈금 위에서만 매긴다.

    누적 모델을 쓰지 않는 이유는 모듈 도크스트링에 적었다. 원문이 주는 것은 온도가
    높을수록 추대까지 걸리는 날이 짧아진다는 단조 관계와 세 개의 눈금이다.
    """
    if mean_temp is None:
        return None, {}
    score = cultivar_fit._piecewise(mean_temp, [
        (15.0, 100.0),      # "15℃ 이하에서는 추대 크게 지연"
        (20.0, 70.0),       # "20℃ 20일"  - 작기 길이와 비슷해 관리가 필요한 구간
        (25.0, 25.0),       # "25℃ 파종 10일 만에 추대" - 수확 전에 추대가 온다
        (30.0, 0.0),
    ])
    if mean_temp <= 15:
        note = "15℃ 이하로 추대가 크게 지연되는 구간이에요"
    elif mean_temp <= 20:
        note = "추대까지 20~30일이 걸리는 구간이에요"
    elif mean_temp <= 25:
        note = "추대까지 10~20일로 짧아지는 구간이에요"
    else:
        note = "파종 10일 안에 추대할 수 있는 고온 구간이에요"
    return score, {"작기평균기온": round(mean_temp, 1), "판정": note,
                   "기준": "작물표준 상추 bolting_risk(25℃ 10일 / 20℃ 20일 / 15℃ 30일)"}


def score_frost(frost_days, structure):
    """작기 중 서리일수(일 최저 0℃ 이하). 시설 작형에서는 난방·보온 부담으로 읽는다."""
    if frost_days is None:
        return None, {}
    score = cultivar_fit._piecewise(frost_days, [
        (0.0, 100.0), (3.0, 85.0), (10.0, 60.0), (25.0, 30.0), (50.0, 0.0),
    ])
    return score, {"작기서리일수": round(frost_days, 1),
                   "기준": "일 최저기온 0℃ 이하",
                   "읽기": "난방·보온 부담" if structure == STRUCT_FACILITY else "직접 저온 피해"}


def score_heat(hot_days, hot_limit_days, hot_c, hot_limit_c):
    """오이 고온 장해. 자료의 growth_stress_temperature_c(high 30 / high_limit 35)를 쓴다."""
    if hot_days is None:
        return None, {}
    penalty = min(hot_days * 2.5, 55.0) + min((hot_limit_days or 0) * 8.0, 40.0)
    return max(0.0, 100.0 - penalty), {
        f"{hot_c:.0f}℃초과일수": round(hot_days, 1),
        f"{hot_limit_c:.0f}℃초과일수": round(hot_limit_days or 0.0, 1),
        "기준": f"{hot_c:.0f}℃ 이상 생육 스트레스 · {hot_limit_c:.0f}℃ 이상 생육 중지",
        "감점": round(penalty, 1),
    }


def score_cold(cold_days, low_stress_c):
    """오이 저온 장해. 자료의 growth_stress_temperature_c.low(15℃)를 기준으로 본다."""
    if cold_days is None:
        return None, {}
    penalty = min(cold_days * 1.2, 100.0)
    return max(0.0, 100.0 - penalty), {
        f"{low_stress_c:.0f}℃미만일수": round(cold_days, 1),
        "기준": f"일 최저기온 {low_stress_c:.0f}℃ 미만에서 생육 스트레스",
        "감점": round(penalty, 1),
    }


def score_season_rain(crop, season_name, rain_mm, heavy_days, window_days):
    """작기 강수. 부족 기준은 감자와 같은 reference_data 기준값을 작기 길이로 비례 조정해
    쓰고(cultivar_fit.score_rain 재사용), 과습은 집중강수일수로 본다."""
    ref = RAIN_REF_SEASON.get(crop, {}).get(season_name)
    score, detail = cultivar_fit.score_rain(rain_mm, heavy_days, window_days, crop, ref)
    if detail:
        detail["기준작형"] = ref
    return score, detail


# ═══════════════════════════════════════════════════════════════
# 4. 작형 1건 채점
# ═══════════════════════════════════════════════════════════════

def _crop_thresholds(crop, payload):
    """채점에 쓸 온도 기준. 품종 데이터의 recommended_environment를 1순위로 쓴다
    (품종 문장과 같은 자료여서 화면에서 숫자가 어긋나지 않는다)."""
    env = ((payload.get("common_management") or {}).get("recommended_environment")) or {}
    growth = env.get("growth_temperature_c") or {}
    lo, hi = growth.get("min"), growth.get("max")
    out = {"growth_lo": lo, "growth_hi": hi,
           "growth_basis": "품종자료 recommended_environment.growth_temperature_c"}
    if crop == "오이":
        stress = env.get("growth_stress_temperature_c") or {}
        out.update({
            "day_lo": 22, "day_hi": 28,
            "day_basis": "작물표준 오이 growing_optimal_day 22~28℃",
            "night_lo": 15, "night_hi": 18,
            "night_basis": "작물표준 오이 growing_optimal_night 15~18℃",
            "low_stress": stress.get("low"), "hot": stress.get("high"),
            "hot_limit": stress.get("high_limit"),
        })
    return out


def _score_candidate(crop, spec, metrics, th):
    """한 파종일 후보의 (총점, breakdown, 제외항목)."""
    weights = WEIGHTS[crop]
    items = {}

    items["생육적온"] = score_optimal_temp(
        metrics.get("tavg"), th["growth_lo"], th["growth_hi"], th["growth_basis"])

    if crop == "상추":
        items["추대위험"] = score_bolting(metrics.get("tavg"))
        items["서리저온"] = score_frost(metrics.get("frost_days"), spec["structure"])
    else:
        items["주간적온"] = score_optimal_temp(
            metrics.get("tmax"), th["day_lo"], th["day_hi"], th["day_basis"])
        # 밤 적온은 폭을 넓게 본다. 일 최저기온은 새벽 한때의 값이라 하루 내내
        # 그 온도인 것이 아니고, 노지에서 15~18℃ 안에 드는 날은 원래 드물다.
        items["야간적온"] = score_optimal_temp(
            metrics.get("tmin"), th["night_lo"], th["night_hi"], th["night_basis"], scale=1.5)
        items["고온장해"] = score_heat(metrics.get("hot_days"), metrics.get("hot_limit_days"),
                                   th["hot"] or 30.0, th["hot_limit"] or 35.0)
        items["저온장해"] = score_cold(metrics.get("cold_days"), th["low_stress"] or 15.0)

    # 시설 작형은 강수 축을 뺀다 - 하우스 안에는 비가 오지 않는다.
    if spec["structure"] == STRUCT_FACILITY:
        items["작기강수"] = (None, {"제외": "시설 작형이라 노지 강수로 판정하지 않아요"})
    else:
        items["작기강수"] = score_season_rain(
            crop, spec["name"], metrics.get("rain_mm"), metrics.get("heavy_days"),
            metrics.get("days"))

    used = {k: v for k, v in items.items() if v[0] is not None}
    excluded = [k for k in items if k not in used]
    total_w = sum(weights[k] for k in used) or 1
    total = sum(v[0] * weights[k] for k, v in used.items()) / total_w
    breakdown = {k: {"점수": round(v[0], 1),
                     "가중치": round(weights[k] * 100 / total_w, 1),
                     "근거": v[1]}
                 for k, v in used.items()}
    for k in excluded:
        breakdown[k] = {"점수": None, "가중치": 0, "근거": items[k][1]}
    return round(total, 1), breakdown, excluded


def _best_planting(crop, spec, clim, th):
    """파종기를 훑어 가장 좋은 파종일을 고른다(감자와 같은 방식)."""
    n = season_window.mmdd_diff(spec["sow_to"], spec["sow_from"])
    if n < 0:
        n += 365
    best = None
    for off in range(0, n + 1, SCAN_STEP_DAYS):
        mmdd = season_window.mmdd_add(spec["sow_from"], off)
        metrics = cycle_metrics(
            clim, mmdd, spec["days"],
            low_stress_c=th.get("low_stress"),
            hot_c=th.get("hot") or 30.0, hot_limit_c=th.get("hot_limit") or 35.0,
            heat_base_c=th.get("growth_lo"))
        if not metrics:
            continue
        score, breakdown, excluded = _score_candidate(crop, spec, metrics, th)
        cand = {"score": score, "breakdown": breakdown, "excluded": excluded,
                "metrics": metrics}
        if best is None or score > best["score"]:
            best = cand
    return best


# ═══════════════════════════════════════════════════════════════
# 5. 작형별 품종 제안
# ═══════════════════════════════════════════════════════════════

def _varieties_for(crop, spec, payload):
    """작형에 어울리는 품종 목록. 근거가 있는 것만 근거를 달고, 없으면 없다고 말한다."""
    varieties = payload.get("varieties") or []
    guide = payload.get("selection_guide") or []
    out = []

    if crop == "오이":
        wanted = CUCUMBER_SEASON_GUIDE.get(spec["name"]) or []
        by_group = {}
        for g in guide:
            if g.get("condition") in wanted and g.get("recommended_group"):
                by_group.setdefault(g["recommended_group"], g)
        for v in varieties:
            g = by_group.get(v["name"])
            if not g:
                continue
            out.append({"name": v["name"],
                        "headline": v.get("headline") or "",
                        "reason": g.get("reason") or "",
                        "basis": f"품종자료 선택기준 '{g.get('condition')}'"})
        if out:
            return out, ""

    # 상추(자료에 작형별 품종 구분 없음) 또는 오이에서 매칭이 없을 때: 전 품종을 담되
    # 작기 길이로 걸러 근거를 붙인다. 품종 생육일수는 품종 데이터의 growth_days다.
    for v in varieties:
        gd = v.get("growth_days") or {}
        lo = gd.get("min")
        note = ""
        if lo and lo > spec["days"]:
            note = (f"이 품종의 생육기간({lo}~{gd.get('max') or lo}일)이 "
                    f"이 작형의 작기({spec['days']}일)보다 길어요")
        out.append({"name": v["name"], "headline": v.get("headline") or "",
                    "reason": note, "basis": "품종자료 생육기간" if note else ""})
    hint = ("자료에 작형별로 어떤 품종을 쓰라는 구분이 없어요. 세 품종 모두 이 작형에 쓸 수 "
            "있고, 고온기에는 내서성·만추대성 품종을 고르라고만 적혀 있습니다."
            if crop == "상추" else "")
    return out, hint


# ═══════════════════════════════════════════════════════════════
# 6. 근거 문장
# ═══════════════════════════════════════════════════════════════

def _reasons(crop, spec, best, th, region, clim):
    """작형 1건의 (pros, cons). 프런트엔드가 쓰는 {text, basis} 모양을 맞춘다."""
    pros, cons = [], []
    m, bd = best["metrics"], best["breakdown"]
    station = region.get("station_name") or "관측소"
    years = m.get("years_used") or 0
    base = f"{station} 평년 {years}년"

    def add(target, text, basis):
        target.append({"text": text, "basis": basis})

    # 재배 구조를 맨 앞에 둔다 - 초보자에게는 "하우스가 필요한가"가 점수보다 앞선다.
    if spec["structure"] == STRUCT_FACILITY:
        hd = m.get("heating_degree")
        text = (f"하우스 안에서 키우는 작형이에요. 점수는 '바깥 기상이 적온에 맞는 정도'라서, "
                f"낮게 나온 만큼 난방·보온으로 메워야 한다는 뜻입니다")
        if hd:
            text += f" (작기 난방도일 약 {hd:,.0f}℃·일)"
        add(cons, text, f"작형 구분 '{spec['region_text']}' · {base}")
    elif spec["structure"] == STRUCT_BOTH:
        add(pros, f"노지로도 시설로도 하는 작형이에요({spec['region_text']}). 초기에는 노지로 "
                  f"시작하고 늦추위가 오면 보온하는 식으로 조절할 수 있어요",
            f"작형 구분 '{spec['region_text']}'")
    else:
        add(pros, f"하우스 없이 노지에서 하는 작형이에요({spec['region_text']})",
            f"작형 구분 '{spec['region_text']}'")

    # 생육 적온
    g = bd.get("생육적온") or {}
    if g.get("점수") is not None:
        t = m.get("tavg")
        lo, hi = th["growth_lo"], th["growth_hi"]
        if g["점수"] >= 85:
            add(pros, f"작기 평균기온이 {t:.1f}℃로 생육 적온 {lo}~{hi}℃ 안에 들어요", base)
        elif t is not None and t < lo:
            add(cons, f"작기 평균기온이 {t:.1f}℃로 생육 적온 {lo}~{hi}℃보다 낮아 자람이 느려요", base)
        else:
            add(cons, f"작기 평균기온이 {t:.1f}℃로 생육 적온 {lo}~{hi}℃보다 높아요", base)

    if crop == "상추":
        b = bd.get("추대위험") or {}
        if b.get("점수") is not None:
            note = (b.get("근거") or {}).get("판정") or ""
            src = (b.get("근거") or {}).get("기준") or ""
            (pros if b["점수"] >= 70 else cons).append(
                {"text": f"추대 위험 — {note}", "basis": src})
        f = bd.get("서리저온") or {}
        if f.get("점수") is not None:
            days = m.get("frost_days") or 0
            if days < 1:
                add(pros, "작기 중 서리 내리는 날이 거의 없어요", base)
            elif spec["structure"] == STRUCT_FACILITY:
                add(cons, f"작기 중 서리일이 평년 {days:.0f}일이에요. 그만큼 보온이 필요합니다", base)
            else:
                add(cons, f"작기 중 서리일이 평년 {days:.0f}일이에요. 노지 작형이라 "
                          f"부직포·터널 같은 보온 준비가 필요합니다", base)
    else:
        d = bd.get("주간적온") or {}
        if d.get("점수") is not None:
            tx = m.get("tmax")
            if d["점수"] >= 85:
                add(pros, f"작기 평균 최고기온이 {tx:.1f}℃로 낮 적온 "
                          f"{th['day_lo']}~{th['day_hi']}℃에 들어요", base)
            else:
                add(cons, f"작기 평균 최고기온이 {tx:.1f}℃로 낮 적온 "
                          f"{th['day_lo']}~{th['day_hi']}℃에서 벗어나요", base)
        nt = bd.get("야간적온") or {}
        if nt.get("점수") is not None:
            tn = m.get("tmin")
            if nt["점수"] >= 85:
                add(pros, f"작기 평균 최저기온이 {tn:.1f}℃로 밤 적온 "
                          f"{th['night_lo']}~{th['night_hi']}℃에 들어요", base)
            else:
                add(cons, f"작기 평균 최저기온이 {tn:.1f}℃예요. 오이는 밤 온도를 따로 "
                          f"관리하는 작물이라 {th['night_lo']}~{th['night_hi']}℃를 목표로 합니다", base)
        h = bd.get("고온장해") or {}
        if h.get("점수") is not None and (m.get("hot_days") or 0) >= 1:
            add(cons, f"작기 중 최고기온 {(th['hot'] or 30):.0f}℃를 넘는 날이 평년 "
                      f"{m['hot_days']:.0f}일이에요"
                      + (f" (생육이 멈추는 {(th['hot_limit'] or 35):.0f}℃ 초과는 "
                         f"{m.get('hot_limit_days') or 0:.0f}일)"
                         if (m.get("hot_limit_days") or 0) >= 0.5 else ""),
                f"{base} · 품종자료 growth_stress_temperature_c")
        c = bd.get("저온장해") or {}
        if c.get("점수") is not None:
            cd_ = m.get("cold_days") or 0
            if cd_ < 3:
                add(pros, f"작기 중 최저기온 {(th['low_stress'] or 15):.0f}℃ 미만인 날이 "
                          f"평년 {cd_:.0f}일로 적어요", base)
            else:
                add(cons, f"작기 중 최저기온 {(th['low_stress'] or 15):.0f}℃ 미만인 날이 "
                          f"평년 {cd_:.0f}일이에요", f"{base} · 품종자료 growth_stress_temperature_c")

    # 자료가 '터널조숙재배'라고 적어 둔 작형은 그 사실을 밝힌다. 점수는 노지 기상으로
    # 매겼으므로, 터널 보온을 전제한 작형을 순수 노지로 읽으면 실제보다 낮게 나온다.
    #
    # ⚠️ 작형 이름으로 한 번 더 확인한다. 품종 매핑에서는 억제재배(8월 파종)도 '여름철
    #    노지·터널조숙재배' 항목을 참고하는데(고온기 파종이라 가시오이 근거가 성립한다),
    #    그 문구를 그대로 인용하면 억제재배를 자료가 '터널조숙재배'라고 부른 것처럼 된다.
    #    실제로 그렇게 찍혔다 - 자료에 없는 말을 자료의 말로 내보낸 것이다.
    if crop == "오이" and "조숙" in (spec["name"] or ""):
        add(cons, "자료는 이 작형을 '노지·터널조숙재배'로 적고 있어요. 점수는 순수 노지 "
                  "기상으로 매긴 값이라, 터널이나 부직포로 초기 보온을 하면 실제 조건은 "
                  "이보다 낫습니다", "품종자료 선택기준 '여름철 노지·터널조숙재배'")

    # 수확기 후기. 채점 창은 파종~첫수확이라 그 뒤로 이어지는 수확 후반의 추위를 보지
    # 못한다(오이 억제재배는 첫수확 10월인데 수확기가 1월 하순까지 간다).
    #
    # 기준을 생육 스트레스 하한(오이 15℃)으로 두었더니 나주 12.4℃·제주 14.6℃의 6월
    # 수확기에도 "보온이 필요합니다"가 붙었다. 6월 밤 최저 14℃는 보온할 온도가 아니다.
    # 자료가 따로 주는 '생육이 크게 억제되는' 온도를 쓴다.
    tail_min = mean_in_mmdd_range(clim, spec["harvest_from"], spec["harvest_to"], "minTa")
    tail_limit = GROWTH_SUPPRESSION_C if crop == "오이" else season_window.FROST_TEMP_C
    if tail_min is not None and tail_min < tail_limit:
        add(cons, f"수확기가 {spec['harvest_to'].replace('-', '월 ')}일까지 이어지는 작형이에요. "
                  f"그 구간 평년 최저기온이 {tail_min:.1f}℃라 수확 후반에는 보온이 필요합니다 "
                  f"(점수는 파종~첫수확 {spec['days']}일 구간으로 매겼어요)",
            f"{base} · 수확기 {spec['harvest_from']}~{spec['harvest_to']}")

    # 강수
    r = bd.get("작기강수") or {}
    if r.get("점수") is None:
        add(cons, "하우스 안에는 비가 오지 않아 노지 강수로는 판정하지 않았어요. "
                  "대신 관수 계획이 필요합니다", "가중치 재정규화")
    else:
        rain = m.get("rain_mm") or 0
        heavy = m.get("heavy_days") or 0
        if r["점수"] >= 75:
            add(pros, f"작기 강수가 평년 {rain:,.0f}mm로 무리 없는 편이에요", base)
        else:
            add(cons, f"작기 강수가 평년 {rain:,.0f}mm이고 하루 50mm 넘는 집중강수가 "
                      f"{heavy:.1f}일이에요. 배수로와 두둑을 미리 잡아 두세요", base)
    return pros, cons


# ═══════════════════════════════════════════════════════════════
# 7. 엔트리
# ═══════════════════════════════════════════════════════════════

def _grade_of(score):
    """For_Frontend.md §3과 같은 4단계."""
    if score is None:
        return None, "산출 불가"
    if score >= 80:
        return "good", "우수"
    if score >= 60:
        return "normal", "양호"
    if score >= 40:
        return "caution", "주의"
    return "bad", "위험"


def score_seasons(region_name, crop, experience="beginner",
                  years=season_window.DEFAULT_YEARS, climatology=None,
                  soil_readings=None, station=None, allow_fetch=True):
    """지역별 작형 순위. breed.md §23"""
    payload = cultivar_data.load_crop(crop)
    if not payload:
        return {"status": "no_data", "crop": crop,
                "error": f"품종 데이터가 아직 없어요: {crop}"}

    if station is None:
        from region_mapper import find_nearest_station
        m = find_nearest_station(region_name)
        # region_mapper의 성공 상태는 "matched"다("ok"가 아니다).
        if not m or m.get("status") != "matched" or not m.get("station"):
            return {"status": (m or {}).get("status", "not_found"), "crop": crop,
                    "error": f"지역을 찾지 못했어요: {region_name} ({(m or {}).get('status')})"}
        station = m["station"]
        distance_km = m.get("distance_km")
    else:
        distance_km = None

    region = {
        "input": region_name,
        "station_id": station["station_id"], "station_name": station.get("station_name"),
        "cluster_id": station.get("cluster_id"), "cluster_name": station.get("cluster_name"),
        "distance_km": distance_km,
    }
    clim = climatology or season_window.station_climatology(
        station["station_id"], years=years, allow_fetch=allow_fetch)
    if not clim or clim.get("status") != "ok":
        return {"status": "insufficient", "crop": crop, "region": region,
                "error": (clim or {}).get("message") or "평년 기상자료가 부족해요"}

    th = _crop_thresholds(crop, payload)
    if th.get("growth_lo") is None:
        return {"status": "no_data", "crop": crop, "region": region,
                "error": f"{crop} 생육 적온 자료가 없어 작형을 채점할 수 없어요"}

    # 고랭지 작형은 고랭지 지역에서만 후보로 둔다(감자와 같은 기준을 쓴다).
    row = cultivar_fit._climate_row(station["station_id"]) or {}
    elevation = cultivar_fit._num(row.get("elevation"))
    is_highland = ((elevation is not None and elevation >= cultivar_fit.HIGHLAND_MIN_ELEVATION_M)
                   or station.get("cluster_id") in cultivar_fit.HIGHLAND_CLUSTER_IDS)

    ranking, skipped = [], []
    for spec in season_specs(crop):
        if "고랭지" in spec["name"] and not is_highland:
            skipped.append({
                "season": spec["name"],
                "reason": (f"고랭지 작형이라 표고 {cultivar_fit.HIGHLAND_MIN_ELEVATION_M}m 이상 "
                           f"또는 고랭지 기후대에서만 권해요"
                           + (f" (이 지역 표고 {elevation:.0f}m)" if elevation is not None else "")),
            })
            continue
        best = _best_planting(crop, spec, clim, th)
        if not best:
            skipped.append({"season": spec["name"], "reason": "평년 일자료가 부족해 채점하지 못했어요"})
            continue

        grade, grade_label = _grade_of(best["score"])
        pros, cons = _reasons(crop, spec, best, th, region, clim)
        varieties, variety_hint = _varieties_for(crop, spec, payload)
        m = best["metrics"]
        badges = [f"{spec['structure']} · {spec['period_text']}"]
        blockers = []
        # 자료가 지역을 좁혀 놓은 작형('제주·남부 시설')은 그 문구를 배지로 그대로 남기고,
        # 경고는 **이 지역에서 실제로 계산된 값**이 나쁠 때만 붙인다. '남부'의 경계를
        # 우리가 정할 근거가 없어서다. 처음에는 지역과 무관하게 경고를 달았는데, 그래서
        # 제주(3위 81.7점, 바로 그 작형의 본거지)에도 "이 지역에서 하려면 난방 계획을 더
        # 봐야 합니다"가 붙었다.
        if any(k in spec["region_text"] for k in ("제주", "남부")):
            badges.append(f"자료 기준 지역: {spec['region_text']}")
            temp_score = ((best["breakdown"].get("생육적온") or {}).get("점수"))
            if temp_score is not None and temp_score < 50:
                blockers.append(
                    f"자료는 이 작형을 '{spec['region_text']}' 작형으로 적고 있어요. "
                    f"여기 작기 평균기온은 {m['tavg']:.1f}℃로 적온 "
                    f"{th['growth_lo']}~{th['growth_hi']}℃와 차이가 커서 난방 부담이 큽니다")

        ranking.append({
            "row_kind": "season",
            "name": spec["name"],
            "cultivation_type": spec["name"],
            "structure": spec["structure"],
            "score": best["score"], "grade": grade, "grade_label": grade_label,
            "planting_window": {
                "from": spec["sow_from"], "to": spec["sow_to"], "best": m["plant"],
                "harvest": m["harvest"], "days": m["days"],
            },
            "harvest_window": {"from": spec["harvest_from"], "to": spec["harvest_to"]},
            "metrics": {
                "작기평균기온": round(m["tavg"], 1),
                "작기평균최고기온": round(m["tmax"], 1),
                "작기평균최저기온": round(m["tmin"], 1),
                "작기강수mm": round(m.get("rain_mm") or 0),
                "집중강수일수": round(m.get("heavy_days") or 0, 1),
                "서리일수": round(m.get("frost_days") or 0, 1),
                "난방도일": round(m.get("heating_degree") or 0),
                "평년연수": m.get("years_used"),
            },
            "breakdown": best["breakdown"],
            "excluded": best["excluded"],
            "pros": pros, "cons": cons,
            "badges": badges, "blockers": blockers,
            "varieties": varieties, "variety_hint": variety_hint,
            "reasons": [p["text"] for p in pros[:2]],
            "cautions": [c["text"] for c in cons[:2]],
        })

    if not ranking:
        return {"status": "insufficient", "crop": crop, "region": region,
                "error": "채점할 수 있는 작형이 없었어요", "skipped": skipped}

    ranking.sort(key=lambda r: -(r["score"] or 0))
    tie_note = ""
    if len(ranking) >= 2:
        gap = (ranking[0]["score"] or 0) - (ranking[1]["score"] or 0)
        if gap < CLOSE_SCORE_GAP:
            tie_note = (f"1위 {ranking[0]['name']}와 2위 {ranking[1]['name']}의 점수 차가 "
                        f"{gap:.1f}점이라 사실상 비슷해요. 둘 중에는 손이 덜 가는 쪽을 "
                        f"고르시면 됩니다.")
    return {
        "status": "matched",
        "crop": crop,
        "unit": "작형",
        "scoring_mode": cultivar_data.SCORING_SEASON,
        "score_label": "노지 기상 적합도",
        "region": region,
        "region_metrics": {
            "station_name": region["station_name"],
            "years_used": len(clim.get("years") or []),
            "cluster_name": region.get("cluster_name"),
        },
        "ranking": ranking,
        "skipped": skipped,
        "tie_note": tie_note,
        # ⚠️ 마크다운(**강조**)을 쓰지 않는다. 화면 템플릿은 이 문구를 그대로 글자로
        #    찍어서 "**바깥(노지) 기상**이"처럼 별표가 보인다(실제로 그렇게 나왔다).
        "note": ("품종이 아니라 '작형'을 줄 세웠어요. 상추·오이는 품종 자료에 품종별 기후 "
                 "수치가 없어 품종끼리는 점수가 갈리지 않고, 대신 작형마다 파종기가 달라 "
                 "작기 기상이 크게 갈립니다."),
        "score_note": ("점수는 그 작기 동안 바깥(노지) 기상이 이 작물의 적온에 얼마나 맞는지를 "
                       "뜻해요. 노지 작형에서는 그것이 곧 재배 조건이고, 시설 작형에서는 "
                       "하우스가 난방·보온으로 메워야 하는 몫입니다."),
        "cautions": cultivar_data.dataset_cautions(crop),
        "source": payload.get("source_file"),
        "climate_note": (f"평년 {len(clim.get('years') or [])}년 ASOS 일자료 "
                         f"(관측소 {region['station_name']}) — 올해·예보 미사용"),
    }
