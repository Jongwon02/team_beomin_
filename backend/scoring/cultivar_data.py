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


def _growth_days(raw):
    """growth_period_days를 (대표 min/max, 작형별 min/max)로 푼다.

    두 형태를 받는다:
      · {"min":80, "max":90, "note":...}            (추백·자영·수미)
      · {"spring":{"min":90,"max":100}, "summer":{...}}  (대서)
    """
    if not isinstance(raw, dict):
        return (None, None), {}, None

    by_season = {}
    for key, season in _GROWTH_SEASON_KEYS.items():
        if isinstance(raw.get(key), dict):
            lo, hi = _range(raw[key])
            if lo is not None:
                by_season[season] = (lo, hi if hi is not None else lo)

    if by_season:
        lows = [v[0] for v in by_season.values()]
        highs = [v[1] for v in by_season.values()]
        return (min(lows), max(highs)), by_season, raw.get("note")

    lo, hi = _range(raw)
    return (lo, hi), {}, raw.get("note")


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

def _normalize_variety(crop, raw, std):
    """원본 품종 레코드 1건 -> 점수/응답에서 쓰는 형태.

    std는 crop_standards_v2.json[crop] (폴백 원천).
    """
    env = raw.get("recommended_environment") or {}
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
    ph = env.get("soil_ph")
    if isinstance(ph, dict) and ph.get("min") is not None:
        ph_lo, ph_hi, ph_src = ph.get("min"), ph.get("max"), "품종"
    else:
        std_ph = std_soil.get("ph") or {}
        ph_lo, ph_hi = std_ph.get("optimal_min"), std_ph.get("optimal_max")
        ph_src = "작물표준"

    # 고온 한계: 작물 표준의 high_temp_risk(감자 25℃ 비대 둔화 시작)를 쓴다.
    # 품종 파일에는 고온 임계가 수치로 없다(서술만) - 없는 값을 만들지 않는다.
    high_risk = std_temp.get("high_temp_risk") or {}
    hot_threshold = high_risk.get("threshold")

    (gd_lo, gd_hi), gd_by_season, gd_note = _growth_days(raw.get("growth_period_days"))
    disorders = raw.get("physiological_disorders") or []
    storage = raw.get("storage_and_sales") or {}
    harvest = raw.get("harvest") or {}
    tuber = raw.get("tuber_characteristics") or {}

    name = raw.get("name_ko") or raw.get("id")
    aliases = [a for a in (raw.get("alternative_name"), raw.get("name_en"), raw.get("id")) if a]

    return {
        "crop": crop,
        "id": raw.get("id"),
        "name": name,
        "aliases": aliases,
        "category": raw.get("category") or [],
        "maturity": raw.get("maturity"),

        # ── 채점에 쓰는 값 ──
        "growth_days": {"min": gd_lo, "max": gd_hi, "note": gd_note},
        "growth_days_by_season": gd_by_season,
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
        "headline": tuber.get("texture") or tuber.get("processing_quality"),
        "tuber": tuber,
        "primary_use": raw.get("primary_use") or [],
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
        "beginner_friendly": bool(raw.get("recommended_for_beginner")),
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
    varieties = [_normalize_variety(crop, v, std) for v in raw.get("varieties") or []]

    payload = {
        "crop": crop,
        "varieties": varieties,
        "dataset": raw.get("dataset") or {},
        "common_management": raw.get("common_management") or {},
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


def dataset_cautions(crop):
    """데이터 제공자가 붙인 주의문(파종일·시비량은 지역에 따라 다르다 등).

    화면·챗봇 답변에 그대로 실어야 하는 문구다.
    """
    payload = load_crop(crop)
    if not payload:
        return []
    return payload["dataset"].get("caution") or []
