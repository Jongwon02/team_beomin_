# -*- coding: utf-8 -*-
"""품종 기준 데이터 로더 (breed.md §4의 L2 계층).

원본은 `data/cultivars/<작물>.json`이다. 사용자가 준 파일 형식(dataset /
common_management / varieties)을 **그대로 보존**하고, 점수 계산에 필요한 형태로는
여기서 정규화해서 넘긴다. 원본을 우리 스키마에 맞춰 고쳐 쓰면 나중에 데이터가
갱신될 때마다 손으로 변환해야 하고, 그 과정에서 수치가 바뀐다.

정규화가 하는 일 3가지
  1. **작물 표준 폴백** — 품종 파일에 없는 값은 `crop_standards_v2.json[작물]`에서
     가져온다(breed.md §4.2). 자영의 soil_ph가 null인 것은 "모르는 값"이 아니라
     "감자 공통값(5.0~6.0)을 쓴다"는 뜻이다.
  2. **모양이 다른 필드 통일** — 대서만 growth_period_days가
     {spring:{...}, summer:{...}} 형태다. 작형별 생육일수로 풀어 담고, 작형 지정이
     없을 때 쓸 대표값(min/max)도 함께 만든다.
  3. **자유서술 → 판정 플래그** — recommended_season 같은 한국어 문장을 작형 코드로
     매핑하고, "고온이 원인인 생리장해가 있는가" 같은 채점 플래그를 규칙으로 뽑는다.
     규칙은 아래 상수에 모아 두었다(코드 곳곳에 문자열 매칭을 흩뿌리지 않는다).

⚠️ 여기서 수치를 새로 만들지 않는다. 없으면 None으로 남기고, 점수 쪽에서 해당
   항목을 제외한 뒤 가중치를 재정규화한다.
"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]          # 저장소 루트
CULTIVAR_DIR = BASE_DIR / "data" / "cultivars"
CROP_STANDARDS_PATH = BASE_DIR / "crop_standards_v2.json"
REPORT_DIR = BASE_DIR / "data" / "cultivar_reports"

# ── 작형(재배 작기) 정규화 ────────────────────────────────────────────────
# 카노니컬 이름은 reference_data.CULTIVATION_TYPES(감자: 봄재배/고랭지재배)와 일부러
# 같게 맞췄다. 작물 점수 쪽과 용어가 갈리면 화면에서 두 개의 작형 어휘가 섞인다.
# '가을재배'는 작물 점수 엔진에는 없는 작형이다(감자 기준값이 봄·고랭지만 있음) -
# 품종 계층은 ASOS 실측으로 자체 계산하므로 다룰 수 있다.
SEASON_SPRING = "봄재배"
SEASON_HIGHLAND = "고랭지재배"
SEASON_FALL = "가을재배"
SEASON_FACILITY = "시설재배"

_SEASON_PATTERNS = [
    (SEASON_HIGHLAND, ("고랭지", "준고랭지", "여름재배")),
    (SEASON_FALL, ("가을",)),
    (SEASON_FACILITY, ("시설", "겨울")),
    (SEASON_SPRING, ("봄", "조기재배")),
]

# 원본의 growth_period_days 키(대서) → 카노니컬 작형
_GROWTH_SEASON_KEYS = {
    "spring": SEASON_SPRING,
    "summer": SEASON_HIGHLAND,
    "fall": SEASON_FALL,
    "autumn": SEASON_FALL,
}

# ── 자유서술에서 채점 플래그를 뽑는 규칙 ──────────────────────────────────
# late_cool_preferred: 생육 후반이 서늘해야 '품질'이 올라가는 품종인가.
#   자영처럼 색소(안토시아닌)를 상품성으로 삼는 품종은 수량과 별개로 후기 저온이
#   유리하다는 근거가 데이터에 들어 있다(regional_notes / special_component).
_LATE_COOL_CATEGORY_HINTS = ("기능성", "컬러")
_LATE_COOL_COMPONENT_HINTS = ("안토시아닌", "색소")
# early_market_preferred: '조기 출하'가 재배 목적인 품종인가.
#   추백처럼 극조생 조기출하용 품종은 늦게 캐면 품종을 고른 이유 자체가 사라진다
#   (장마·고온을 맞고, 노지감자 성출하기와 겹쳐 값도 떨어진다). 수량 항목과 별개로
#   '수확 시점'을 평가해야 하는 유일한 근거가 이 서술이다.
_EARLY_MARKET_HINTS = ("조기 출하", "조기출하")
# heat_disorder_sensitive: 고온이 '원인'으로 적힌 생리장해가 있는가(자영 색발현 저하,
#   대서 내부갈변). 이 품종은 비대기 고온일수 감점을 더 무겁게 준다.
_HEAT_CAUSE_HINTS = ("고온", "지나치게 더", "토양온도")

_RISK_LEVEL_NORMALIZE = {
    "높음": "높음", "매우 높음": "높음",
    "중간": "중간", "보통": "중간", "관리 필요": "중간",
    "낮음": "낮음",
}

# ── 작물별 채점 모드 ──────────────────────────────────────────────────────
# breed.md §6의 기후 채점은 "파종 → 생육일수 → 수확"이 성립하는 1년생 작물만 대상이다.
#   climate    - 파종일을 훑어 지역 기상으로 순위를 낸다(감자. cultivar_fit.score_cultivars)
#   conditions - 순위를 점수로 내지 않고, 데이터에 적힌 선택조건·주의사항으로 추천한다
#
# 감자 외 4작물을 climate로 두지 않는 이유는 데이터가 그 축을 지지하지 않기 때문이다.
#   사과·배 : 다년생이라 파종일이 없다. growth_period_days도 '만개후일수'여서 무상기간과
#             비교할 대상이 아니다(아래 _growth_days 주석).
#   오이     : 품종별 환경 수치가 아예 없다(공통값만) - 기후로 매기면 3품종이 동점이 된다.
#   상추     : 품종별 환경값은 있으나 파종~수확 일수가 '보통/불확실' 추정치이고 3품종 중
#             1개(로메인)는 불확실이다. 근거가 고른 축이 아니라 순위 근거로 쓰지 않는다.
# 근거 없는 축으로 순위를 만들면 화면에 "1위"가 뜨는데 그 1위에 이유가 없다.
SCORING_CLIMATE = "climate"
SCORING_CONDITIONS = "conditions"

CROP_SCORING_MODE = {
    "감자": SCORING_CLIMATE,
    "상추": SCORING_CONDITIONS,
    "오이": SCORING_CONDITIONS,
    "사과": SCORING_CONDITIONS,
    "배": SCORING_CONDITIONS,
}

# ── 데이터 제공자가 붙인 confidence 라벨 ──────────────────────────────────
# 원본이 수치마다 '확실 / 보통 / 불확실 / 확인 불가'를 직접 적어 두었다. 불확실 이하로
# 표시된 값은 **채점에 쓰지 않는다**(breed.md §6.7 "없는 수치를 만들지 않는다").
# 서술로는 신뢰도를 함께 붙여 그대로 넘긴다 - 값을 숨기면 사용자가 확인할 길이 없어진다.
_CONFIDENCE_SCORABLE = ("확실", "보통")


def _confidence_of(d):
    """dict에서 confidence 문자열을 꺼낸다. 없으면 None."""
    if not isinstance(d, dict):
        return None
    c = d.get("confidence")
    return c.strip() if isinstance(c, str) and c.strip() else None


def _is_scorable(conf):
    """confidence 라벨이 채점에 쓸 수 있는 수준인가.

    라벨이 아예 없으면(감자처럼) 예전처럼 쓴다 - 라벨 도입 전 데이터를 못 쓰게 만들면
    감자 채점이 통째로 죽는다. 라벨이 있으면 '확실'·'보통'으로 시작할 때만 통과시킨다.
    """
    if not conf:
        return True
    return conf.startswith(_CONFIDENCE_SCORABLE)


_cache = {}     # crop -> {"varieties": [...], "raw": {...}, "mtime": float}


# ═══════════════════════════════════════════════════════════════
# 1. 파일 로드
# ═══════════════════════════════════════════════════════════════

def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _crop_standards(crop):
    """crop_standards_v2.json[crop]. 없으면 {}."""
    try:
        return _load_json(CROP_STANDARDS_PATH).get(crop, {}) or {}
    except Exception as e:                                       # noqa: BLE001
        logger.error("[cultivar_data] crop_standards_v2.json 로드 실패: %s", e)
        return {}


def available_crops():
    """품종 데이터가 있는 작물 목록(파일명 = 작물명)."""
    if not CULTIVAR_DIR.exists():
        return []
    return sorted(p.stem for p in CULTIVAR_DIR.glob("*.json"))


# ═══════════════════════════════════════════════════════════════
# 2. 정규화 도우미
# ═══════════════════════════════════════════════════════════════

def _canonical_seasons(texts):
    """['서늘한 봄철 조기재배', '고랭지 여름재배'] -> ['봄재배', '고랭지재배'] (순서 보존, 중복 제거).

    '다양한 재배'처럼 어느 작형인지 알 수 없는 서술은 버린다 - 임의로 전 작형으로
    넓히면 근거 없이 후보가 늘어난다.
    """
    out = []
    for text in texts or []:
        for season, hints in _SEASON_PATTERNS:
            if any(h in text for h in hints):
                if season not in out:
                    out.append(season)
                break
    return out


def _range(d, key="min", key2="max"):
    """{"min":1,"max":2} -> (1, 2). None/빈 dict는 (None, None)."""
    if not isinstance(d, dict):
        return None, None
    return d.get(key), d.get(key2)


# growth_period_days 안에서 '파종~수확 총일수'로 쓸 수 있는 키(오이·상추 형태).
# seedling_/transplant_ 는 단계별 일수라 그것만으로 파종~수확이 되지 않는다.
_TOTAL_DAYS_KEYS = ("sowing_to_harvest_total_days_estimate",)
# metric 서술에 이 말이 들어가면 파종 기준이 아니라 만개 기준이다(과수).
_BLOOM_METRIC_HINTS = ("만개",)
# 단계별 일수(서술용으로만 넘긴다)
_STAGE_DAYS_KEYS = ("seedling_days_sowing_to_transplant", "transplant_to_first_harvest_days",
                    "transplant_to_head_harvest_days", "full_ripeness_days",
                    "korea_early_harvest_days")


def _growth_days(raw):
    """growth_period_days를 채점용과 서술용으로 분리해 푼다.

    이 필드는 **이름이 같아도 의미가 작물마다 다르다.** 그대로 쓰면 조용히 틀린다.
      · 감자    : 파종~수확 일수          {"min":80,"max":90} / {"spring":{...},"summer":{...}}
      · 사과·배 : 만개~수확(만개후일수)    {"metric":"만개~수확 일수...","min":188,"max":204}
      · 오이·상추: 육묘일수 + 정식후일수를 작형별로 쪼갠 형태
                  {"seedling_days_sowing_to_transplant":{...},
                   "transplant_to_first_harvest_days":{...},
                   "sowing_to_harvest_total_days_estimate":{"min":45,"max":65}}

    ⚠️ 만개후일수를 파종~수확 생육일수로 쓰면 무상기간 하드 게이트가 오작동한다.
       후지 188~204일을 평창 무상기간 183일과 비교해 "이 지역에서 재배 불가"라는 거짓
       결론이 난다. 사과는 4월에 피고 10월에 따는 다년생이지, 188일을 심어 기르는
       작물이 아니다. 그래서 만개 기준값은 days가 아니라 bloom에 담는다.

    반환 dict
      days      {"min","max","note"}  파종~수확. 채점에 쓸 수 있을 때만 채운다.
      by_season {작형: (min,max)}      작형별 파종~수확(대서)
      bloom     {"min","max","confidence","note"} | None   만개후일수(과수)
      stages    {키: 원본}             육묘·정식후 등 단계별 원본(서술 전용)
      scorable  bool                  days를 채점에 써도 되는가
    """
    empty = {"days": {"min": None, "max": None, "note": None}, "by_season": {},
             "bloom": None, "stages": {}, "scorable": False}
    if not isinstance(raw, dict):
        return empty

    note = raw.get("note")
    conf = _confidence_of(raw)
    metric = raw.get("metric") or ""
    stages = {k: raw[k] for k in _STAGE_DAYS_KEYS if isinstance(raw.get(k), dict)}

    # 1) 작형별 파종~수확 (대서: spring/summer)
    by_season = {}
    for key, season in _GROWTH_SEASON_KEYS.items():
        if isinstance(raw.get(key), dict):
            lo, hi = _range(raw[key])
            if lo is not None:
                by_season[season] = (lo, hi if hi is not None else lo)
    if by_season:
        lows = [v[0] for v in by_season.values()]
        highs = [v[1] for v in by_season.values()]
        return {"days": {"min": min(lows), "max": max(highs), "note": note},
                "by_season": by_season, "bloom": None, "stages": stages, "scorable": True}

    # 2) 만개후일수(과수) - 파종 기준이 아니므로 days에 넣지 않는다
    if any(h in metric for h in _BLOOM_METRIC_HINTS):
        lo, hi = _range(raw)
        bloom = None
        if lo is not None:
            bloom = {"min": lo, "max": hi if hi is not None else lo,
                     "confidence": conf, "note": note, "metric": metric}
        return {"days": {"min": None, "max": None, "note": note},
                "by_season": {}, "bloom": bloom, "stages": stages, "scorable": False}

    # 3) 파종~수확 총일수가 별도 키로 들어온 형태(오이·상추)
    for key in _TOTAL_DAYS_KEYS:
        sub = raw.get(key)
        if isinstance(sub, dict):
            lo, hi = _range(sub)
            if lo is not None:
                sub_conf = _confidence_of(sub) or conf
                return {"days": {"min": lo, "max": hi if hi is not None else lo,
                                 "note": sub.get("note") or note},
                        "by_season": {}, "bloom": None, "stages": stages,
                        "scorable": _is_scorable(sub_conf)}

    # 4) 평평한 {min,max} (감자 추백·자영·수미)
    lo, hi = _range(raw)
    if lo is None:
        return {"days": {"min": None, "max": None, "note": note},
                "by_season": {}, "bloom": None, "stages": stages, "scorable": False}
    return {"days": {"min": lo, "max": hi, "note": note},
            "by_season": {}, "bloom": None, "stages": stages,
            "scorable": _is_scorable(conf)}


def _diseases(raw_list):
    """disease_and_pest_risks -> [{name, level(높음/중간/낮음), symptoms, management}]."""
    out = []
    for d in raw_list or []:
        level_raw = (d.get("risk_level") or "").strip()
        out.append({
            "name": d.get("name"),
            "level": _RISK_LEVEL_NORMALIZE.get(level_raw, "중간" if level_raw else None),
            "level_raw": level_raw or None,
            "symptoms": d.get("symptoms") or [],
            "management": d.get("management") or [],
        })
    return out


def _heat_disorder_sensitive(disorders):
    """생리장해의 '원인'에 고온이 적혀 있으면 True (자영 색발현 저하, 대서 내부갈변)."""
    for d in disorders or []:
        for cause in d.get("causes") or []:
            if any(h in cause for h in _HEAT_CAUSE_HINTS):
                return True
    return False


def _late_cool_preferred(variety):
    """생육 후반 저온이 '품질'에 유리한 품종인가 (수량과 별개의 상품성 축)."""
    cats = variety.get("category") or []
    if any(any(h in c for h in _LATE_COOL_CATEGORY_HINTS) for c in cats):
        return True
    comp = (variety.get("tuber_characteristics") or {}).get("special_component") or ""
    return any(h in comp for h in _LATE_COOL_COMPONENT_HINTS)


def _early_market_preferred(variety):
    """조기 출하가 목적인 품종인가 (category / primary_use 서술로 판정)."""
    haystack = list(variety.get("category") or []) + list(variety.get("primary_use") or [])
    return any(any(h in text for h in _EARLY_MARKET_HINTS) for text in haystack)


# 작물마다 '특징 한 줄'이 담긴 곳이 다르다. 앞에서 찾은 것을 쓴다.
_HEADLINE_SOURCES = (
    ("tuber_characteristics", ("texture", "processing_quality")),   # 감자
    ("fruit", ("texture", "flavor", "skin_color")),                 # 사과
    ("fruit_characteristics", ("texture", "flavor", "shape", "size_class")),  # 배·오이
    ("leaf_characteristics", ("shape", "texture", "color")),        # 상추
    ("plant_characteristics", ("growth_habit", "vine_length")),     # 오이 품종군
    ("tree_characteristics", ("growth_habit", "vigor")),            # 배(과실 서술이 수치뿐일 때)
)
# 작물 공통 환경값이 담긴 키. 사과=environment, 오이·상추=recommended_environment.
_COMMON_ENV_KEYS = ("environment", "recommended_environment")


def _first_dict(raw, keys):
    """주어진 키들 중 앞에서 처음 나오는 dict. 없으면 {}."""
    for k in keys:
        v = raw.get(k)
        if isinstance(v, dict) and v:
            return v
    return {}


def _first_text(v):
    """문자열이면 그대로, 리스트면 앞 2개를 이어 붙인다."""
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, list) and v:
        return " · ".join(str(x) for x in v[:2])
    return None


def _headline(raw):
    """카드에 붙일 특징 한 줄. 감자 tuber / 사과 fruit / 상추 leaf 순으로 찾는다."""
    for field, keys in _HEADLINE_SOURCES:
        d = raw.get(field)
        if not isinstance(d, dict):
            continue
        for k in keys:
            t = _first_text(d.get(k))
            if t:
                return t
    return None


def _maturity_text(raw):
    """maturity를 화면용 문자열로 만든다.

    감자는 "극조생" 문자열인데 사과·배는
    {"class":"만생","harvest_period":"10월 하순~11월 상순"} dict다.
    프런트가 {{ cv.maturity }}로 그대로 찍으므로 dict를 넘기면 [object Object]가 뜬다.
    """
    m = raw.get("maturity")
    if isinstance(m, str):
        return m.strip() or None
    if not isinstance(m, dict):
        return None
    parts = [p for p in (m.get("class"),
                         m.get("harvest_period") or m.get("harvest_date")) if p]
    return " · ".join(parts) or None


def _common_env(common_management):
    """작물 공통 환경값 블록."""
    for k in _COMMON_ENV_KEYS:
        v = (common_management or {}).get(k)
        if isinstance(v, dict):
            return v
    return {}


def _merged_env(raw, common_env):
    """품종별 환경값에 작물 공통값을 보충한다(품종값이 이긴다).

    사과의 품종별 recommended_environment는 산문과 confidence만 담고 수치가 거의 없다
    (후지는 "후지 전용 재배환경 수치는 확인하지 못했다"고 스스로 밝힌다). 수치는
    common_management.environment에만 있으므로 여기서 합친다. 오이·상추는 품종별 항목이
    아예 없어 공통값이 전부다.
    """
    env = raw.get("recommended_environment")
    env = dict(env) if isinstance(env, dict) else {}
    for k, v in (common_env or {}).items():
        env.setdefault(k, v)
    return env


def _ph_range(env):
    """soil_ph를 (min,max)로. 상추는 {"recommended_range":{min,max}}로 한 겹 더 쌓여 있다."""
    ph = env.get("soil_ph")
    if not isinstance(ph, dict):
        return None, None
    if ph.get("min") is not None:
        return ph.get("min"), ph.get("max")
    inner = ph.get("recommended_range")
    if isinstance(inner, dict):
        return inner.get("min"), inner.get("max")
    return None, None


def _report_path(crop, name):
    """이 품종을 다루는 L1 리포트 파일이 있으면 저장소 상대경로를 준다."""
    if not REPORT_DIR.exists():
        return None
    for p in sorted(REPORT_DIR.glob(f"{crop}_*.md")):
        if name in p.stem:
            return str(p.relative_to(BASE_DIR)).replace("\\", "/")
    return None


def _normalize_key(s):
    """품종명 검색 키: 공백·구두점 제거 + 후행 '감자' 제거 + 소문자.

    사용자는 '자영감자', '자 영', 'jayeong'처럼 여러 방식으로 부른다.
    """
    if s is None:
        return ""
    s = re.sub(r"[\s\-_·,]", "", str(s)).lower()
    for crop_suffix in ("감자", "사과", "배", "오이", "상추"):
        if len(s) > len(crop_suffix) and s.endswith(crop_suffix):
            s = s[: -len(crop_suffix)]
            break
    return s


# ═══════════════════════════════════════════════════════════════
# 3. 정규화 본체
# ═══════════════════════════════════════════════════════════════

def _normalize_variety(crop, raw, std, common_env=None):
    """원본 품종 레코드 1건 -> 점수/응답에서 쓰는 형태.

    std는 crop_standards_v2.json[crop] (폴백 원천).
    common_env는 그 작물의 common_management 환경값(품종별 수치가 없을 때 보충).
    """
    env = _merged_env(raw, common_env)
    std_temp = std.get("temperature") or {}
    std_soil = std.get("soil") or {}

    # 생육 적온: 품종값 우선, 없으면 작물 표준(감자 growing_range 14~23)
    g_lo, g_hi = _range(env.get("growth_temperature_c"))
    if g_lo is None:
        g_lo, g_hi = _range(std_temp.get("growing_range"))
        growth_temp_src = "작물표준"
    else:
        growth_temp_src = "품종"

    # 괴경 비대 적온: 품종값 우선, 없으면 작물 표준(tuber_bulking_optimal 15~18)
    b_lo, b_hi = _range(env.get("tuber_bulking_temperature_c"))
    if b_lo is None:
        b_lo, b_hi = _range(std_temp.get("tuber_bulking_optimal"))
        bulking_src = "작물표준"
    else:
        bulking_src = "품종"

    # 토양 산도: 품종값 우선, 없으면 작물 표준(optimal_min/max)
    ph_lo, ph_hi = _ph_range(env)
    if ph_lo is not None:
        ph_src = "품종"
    else:
        std_ph = std_soil.get("ph") or {}
        ph_lo, ph_hi = std_ph.get("optimal_min"), std_ph.get("optimal_max")
        ph_src = "작물표준"

    # 고온 한계: 작물 표준의 high_temp_risk(감자 25℃ 비대 둔화 시작)를 쓴다.
    # 품종 파일에는 고온 임계가 수치로 없다(서술만) - 없는 값을 만들지 않는다.
    high_risk = std_temp.get("high_temp_risk") or {}
    hot_threshold = high_risk.get("threshold")

    gd = _growth_days(raw.get("growth_period_days"))
    disorders = raw.get("physiological_disorders") or []
    # 저장 정보가 담긴 키가 작물마다 다르다: 감자·상추 storage_and_sales / 사과 storage
    # / 배 harvest_and_storage. 앞에서 찾은 것을 쓴다.
    storage = _first_dict(raw, ("storage_and_sales", "storage", "harvest_and_storage"))
    harvest = _first_dict(raw, ("harvest", "harvest_and_storage"))
    tuber = raw.get("tuber_characteristics") or {}

    name = raw.get("name_ko") or raw.get("id")
    aliases = [a for a in (raw.get("alternative_name"), raw.get("name_en"), raw.get("id")) if a]

    return {
        "crop": crop,
        "id": raw.get("id"),
        "name": name,
        "aliases": aliases,
        "category": raw.get("category") or [],
        "maturity": _maturity_text(raw),
        "maturity_raw": raw.get("maturity"),

        # ── 채점에 쓰는 값 ──
        # growth_days는 **파종~수확**만 담는다. 과수의 만개후일수는 bloom_to_harvest로
        # 따로 나간다(섞으면 무상기간 게이트가 오작동한다 - _growth_days 주석 참고).
        "growth_days": gd["days"],
        "growth_days_by_season": gd["by_season"],
        "growth_days_scorable": gd["scorable"],
        "bloom_to_harvest": gd["bloom"],
        "stage_days": gd["stages"],
        "growth_temp": {"min": g_lo, "max": g_hi, "source": growth_temp_src},
        "bulking_temp": {"min": b_lo, "max": b_hi, "source": bulking_src},
        "soil_ph": {"min": ph_lo, "max": ph_hi, "source": ph_src},
        "hot_threshold": hot_threshold,
        "seasons": _canonical_seasons(env.get("recommended_season")),
        "seasons_excluded": _canonical_seasons(env.get("not_recommended_season")),
        "late_cool_preferred": _late_cool_preferred(raw),
        "early_market_preferred": _early_market_preferred(raw),
        "heat_disorder_sensitive": _heat_disorder_sensitive(disorders),
        "diseases": _diseases(raw.get("disease_and_pest_risks")),

        # ── 화면·챗봇에 그대로 넘기는 서술 ──
        "headline": _headline(raw),
        "tuber": tuber,
        # 용도 키도 작물마다 다르다: 감자·상추 primary_use / 사과 market_use.
        "primary_use": raw.get("primary_use") or raw.get("market_use") or [],
        "soil_type": env.get("soil_type") or [],
        "caution_soil": env.get("caution_soil") or [],
        "regional_notes": env.get("regional_notes"),
        "recommended_season_text": env.get("recommended_season") or [],
        "not_recommended_season_text": env.get("not_recommended_season") or [],
        "seed_potato": raw.get("seed_potato_management") or {},
        "cultivation_management": raw.get("cultivation_management") or [],
        "disorders": disorders,
        "harvest": harvest,
        "storage": storage,
        # 필드가 **없는 것**과 False를 구분한다. 사과·배에는 recommended_for_beginner가
        # 아예 없어서 bool(None)=False로 두면 "초보자에게 손이 많이 간다"는 판정을
        # 데이터 없이 지어내게 된다. 없으면 None으로 남기고 호출부가 판단을 미룬다.
        "beginner_friendly": (None if raw.get("recommended_for_beginner") is None
                              else bool(raw.get("recommended_for_beginner"))),
        "beginner_reason": raw.get("beginner_reason"),
        "selection_conditions": raw.get("selection_conditions") or [],
        "key_warnings": raw.get("key_warnings") or [],

        "report": _report_path(crop, name),
        "_raw": raw,
    }


def load_crop(crop):
    """작물 하나의 품종 데이터 전체. 반환 {"crop","varieties","dataset","common_management"}.

    파일이 없으면 None. 파일 mtime이 바뀌면 자동으로 다시 읽는다(서버 재시작 없이
    데이터만 고쳐도 반영되게).
    """
    path = CULTIVAR_DIR / f"{crop}.json"
    if not path.exists():
        return None

    mtime = path.stat().st_mtime
    hit = _cache.get(crop)
    if hit and hit["mtime"] == mtime:
        return hit["payload"]

    raw = _load_json(path)
    std = _crop_standards(crop)
    common = raw.get("common_management") or {}
    common_env = _common_env(common)

    # 오이는 개별 품종이 아니라 '품종군'(다다기·취청·가시)으로 데이터가 짜여 있다.
    # 사용자에게 추천하는 단위가 그 품종군이므로 varieties와 같은 자리에서 읽는다.
    items = raw.get("varieties")
    if not items:
        items = raw.get("variety_groups") or []
        unit = "품종군"
    else:
        unit = "품종"

    varieties = [_normalize_variety(crop, v, std, common_env) for v in items]

    payload = {
        "crop": crop,
        "varieties": varieties,
        "unit": unit,
        "scoring_mode": CROP_SCORING_MODE.get(crop, SCORING_CONDITIONS),
        "dataset": raw.get("dataset") or {},
        "common_management": common,
        "common_environment": common_env,
        "selection_guide": raw.get("selection_guide") or [],
        "llm_response_guidelines": raw.get("llm_response_guidelines") or [],
        "source_file": str(path.relative_to(BASE_DIR)).replace("\\", "/"),
    }
    _cache[crop] = {"mtime": mtime, "payload": payload}
    return payload


def list_varieties(crop):
    """작물의 정규화된 품종 리스트(없으면 [])."""
    payload = load_crop(crop)
    return payload["varieties"] if payload else []


def find_variety(crop, name):
    """품종명/별칭/id로 찾는다. 못 찾으면 None."""
    key = _normalize_key(name)
    if not key:
        return None
    for v in list_varieties(crop):
        keys = {_normalize_key(v["name"]), _normalize_key(v["id"])}
        keys |= {_normalize_key(a) for a in v["aliases"]}
        if key in keys:
            return v
    return None


def variety_names(crop):
    return [v["name"] for v in list_varieties(crop)]


def scoring_mode(crop):
    """이 작물을 기후 점수로 채점할 수 있는가(SCORING_CLIMATE) 아닌가(SCORING_CONDITIONS).

    데이터가 없는 작물도 SCORING_CONDITIONS로 답한다 - 호출부가 None을 따로 다루지
    않게 하고, 실제 '데이터 없음'은 load_crop이 None으로 알린다.
    """
    return CROP_SCORING_MODE.get(crop, SCORING_CONDITIONS)


def is_recommendable(crop, name):
    """추천해도 되는 품종인가.

    추천 가능 집합은 **data/cultivars/<작물>.json 에 실린 품종뿐**이다. 작물 일반
    지식 데이터(crops_for_llm.json)의 major_varieties에는 감자만 24개가 들어 있는데,
    그건 '국내에 이런 품종들이 있다'는 배경 지식이고 우리가 특성을 검수한 목록이
    아니다. 그 목록에서 추천이 새면 근거 없는 품종을 권하게 된다.
    """
    return find_variety(crop, name) is not None


def dataset_cautions(crop):
    """데이터 제공자가 붙인 주의문(파종일·시비량은 지역에 따라 다르다 등).

    화면·챗봇 답변에 그대로 실어야 하는 문구다.
    작물마다 키가 다르다 - 감자·오이·상추는 caution, 사과·배는 notes를 쓴다.
    """
    payload = load_crop(crop)
    if not payload:
        return []
    ds = payload["dataset"]
    out = []
    for key in ("caution", "cautions", "notes"):
        v = ds.get(key)
        if isinstance(v, list):
            out.extend(str(x) for x in v)
        elif isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out
