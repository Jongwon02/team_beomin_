# -*- coding: utf-8 -*-
"""조건 기반 품종 추천 (사과·배·오이·상추).

왜 점수를 매기지 않는가
  cultivar_fit.score_cultivars는 "파종일을 훑어 → 생육일수만큼 자라 → 서리 전에 수확"이
  성립하는 1년생 작물의 채점기다(breed.md §6). 감자는 그 축이 전부 데이터에 있다.
  나머지 4작물은 그렇지 않다 - 필드 이름이 같아도 내용이 그 축을 지지하지 않는다.

    사과·배 : 다년생이다. 파종일이 없고, growth_period_days도 '만개후일수'다.
              이 값을 생육일수로 써서 무상기간과 비교하면 후지(만개후 188~204일)가
              평창(무상기간 183일)에서 '재배 불가'로 찍힌다. 사과는 4월에 피고 10월에
              따는 나무이지 188일을 심어 기르는 작물이 아니다.
    오이     : 품종군별 환경 수치가 아예 없다(작물 공통값만). 기후로 매기면 3품종군이
              전부 동점이 된다. 게다가 주력 작형이 촉성·반촉성 시설재배라 노지 기상으로
              판정하는 것 자체가 틀린다(cultivar_fit.SCORABLE_SEASONS 주석과 같은 이유).
    상추     : 품종별 환경값은 있으나 파종~수확 일수가 추정치이고, 3품종 중 로메인은
              confidence가 '불확실'이다. 근거가 고르지 않은 축으로 1·2·3위를 만들면
              화면에 순위는 뜨는데 그 순위에 이유가 없다.

  그래서 순위 점수 대신 **데이터가 실제로 담고 있는 것**으로 추천한다.
    · selection_conditions - "이런 상황이면 이 품종" (데이터 제공자가 직접 적은 조건)
    · recommended_for_beginner / beginner_reason
    · key_warnings
    · maturity.harvest_period - 과수는 이걸로 착색기 구간을 잡아 지역 판정을 만든다

지역 신호를 반드시 넣는 이유
  화면 제목이 "이 지역에 맞는 품종 추천"이다. 어디서나 같은 목록을 보여주면 제목이
  거짓이 된다. 사과는 **착색기 기온**으로 지역을 가른다 - 수확기가 품종마다 다르므로
  (쓰가루 8월 / 후지 10월 하순) 같은 지역에서도 품종별로 착색기 기온이 달라진다.
  근거는 common_management.environment.coloring_daily_mean_c(12~13℃)다.

  ⚠️ 첫서리로 판정하지 않는다. 한 번 그렇게 짰다가 **청송에서 후지가 부적합**으로
     찍혀서 걷어냈다 - 청송은 국내 최대 후지 주산지다. 자세한 근거는
     _coloring_verdict 도크스트링에 남겼다.

  배는 착색 적온 수치가 데이터에 없어(common_management에 environment가 아예 없다)
  지역 판정을 건너뛴다. 오이·상추도 걸지 않는다 - 주력 작형이 시설이라 노지 기상으로
  판정하면 실재하는 재배를 부정하게 된다. 대신 "작형 선택이 지역보다 앞선다"를 밝힌다.
"""

import logging
import re

import blight_data
import cultivar_data
import cultivar_reasons
import season_window

logger = logging.getLogger(__name__)

# 지역 기후 판정을 시도하는 작물(노지 다년생 과수).
# 오이·상추는 주력 작형이 시설(촉성·반촉성)이라 노지 기상으로 판정하면 실재하는 재배를
# 부정하게 된다 - cultivar_fit.SCORABLE_SEASONS 주석과 같은 이유로 제외한다.
REGION_CHECK_CROPS = ("사과", "배")

# 착색기 길이. 원문이 '착색기'를 일수로 주지 않아 수확 전 30일을 쓰고, 화면 문구에
# '수확 전 30일'임을 그대로 드러낸다(사용자가 근거를 확인할 수 있게).
_COLORING_DAYS = 30


def _range(d):
    """{"min":12,"max":13} -> (12, 13). 아니면 (None, None)."""
    if not isinstance(d, dict):
        return None, None
    return d.get("min"), d.get("max")

# '상순/중순/하순'을 일자 구간으로. 원문이 쓰는 어휘를 그대로 받는다.
_DECADE_RANGE = {"상": (1, 10), "중": (11, 20), "하": (21, 31)}
_MONTH_END = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
              7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}

# "10월 하순", "9월 상·중순", "8월 중·하순"
_DECADE_RE = re.compile(r"(\d{1,2})\s*월\s*([상중하])(?:\s*·\s*([상중하]))?\s*순")
# "9월 1일"
_DAY_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")


def _clamp_day(month, day):
    return max(1, min(day, _MONTH_END.get(month, 30)))


def _mmdd(month, day):
    return f"{month:02d}-{_clamp_day(month, day):02d}"


def _parse_one(text):
    """'10월 하순' / '9월 상·중순' / '9월 1일' -> (시작mmdd, 끝mmdd). 못 읽으면 None."""
    m = _DAY_RE.search(text)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        return _mmdd(mo, d), _mmdd(mo, d)
    m = _DECADE_RE.search(text)
    if m:
        mo = int(m.group(1))
        first, second = m.group(2), m.group(3) or m.group(2)
        lo = _DECADE_RANGE[first][0]
        hi = _DECADE_RANGE[second][1]
        return _mmdd(mo, lo), _mmdd(mo, hi)
    return None


def parse_period(text):
    """수확기 서술을 (시작mmdd, 끝mmdd)로.

    '10월 하순~11월 상순'처럼 물결로 이어진 경우 앞 조각의 시작과 뒤 조각의 끝을 쓴다.
    읽을 수 없으면 None을 준다 - 억지로 날짜를 만들지 않는다.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    parts = re.split(r"\s*[~∼－—-]\s*", text.strip())
    first = _parse_one(parts[0]) if parts else None
    if not first:
        # 앞 조각이 '10월'처럼 순 표기가 없으면 전체에서 한 번 더 찾아본다
        first = _parse_one(text)
        if not first:
            return None
        return first
    if len(parts) > 1:
        last = _parse_one(parts[-1])
        # 뒤 조각에 월이 없으면(예: '10월 상순~중순') 앞 조각의 월을 물려준다
        if not last:
            mo = int(first[0][:2])
            m2 = re.search(r"([상중하])\s*(?:·\s*([상중하]))?\s*순", parts[-1])
            if m2:
                second = m2.group(2) or m2.group(1)
                last = (None, _mmdd(mo, _DECADE_RANGE[second][1]))
        if last:
            return first[0], last[1]
    return first


def harvest_window(variety):
    """품종의 수확기 (시작mmdd, 끝mmdd). maturity 원본에서 읽는다."""
    m = variety.get("maturity_raw")
    if isinstance(m, str):
        return parse_period(m)
    if not isinstance(m, dict):
        return None
    for key in ("harvest_period", "harvest_date"):
        got = parse_period(m.get(key))
        if got:
            return got
    return None


def _window_mean(clim, start_mmdd, end_mmdd, field):
    """기후자료에서 [start, end] 구간 일자료 field의 연도평균. 없으면 None.

    연도별로 먼저 평균을 내고 그 값들을 다시 평균한다(결측이 많은 해가 전체를 끌지
    않게). 구간이 연을 넘지 않는 경우만 다룬다 - 사과·배 착색기는 8~11월이라 넘지 않는다.
    """
    per_year = []
    for _year, records in (clim.get("by_year") or {}).items():
        vals = [r[field] for r in records
                if r.get(field) is not None and start_mmdd <= r["date"][4:6] + "-" + r["date"][6:8] <= end_mmdd]
        if vals:
            per_year.append(sum(vals) / len(vals))
    if not per_year:
        return None
    return round(sum(per_year) / len(per_year), 1)


def _heat_coloring_sensitive(variety):
    """고온에서 착색이 억제되는 것이 이 품종의 알려진 약점인가.

    데이터가 직접 밝힌 경우만 True로 본다(홍로는 recommended_environment에 착색 최적
    25℃와 "착색 초기에 고온에 노출되면 발현이 억제되어 착색불량이 발생하기 쉽다"는
    연구 근거를 달아 두었다). 우리가 품종별 감수성을 추정하지는 않는다.
    """
    env = (variety.get("_raw") or {}).get("recommended_environment")
    if not isinstance(env, dict):
        return False
    if env.get("coloring_optimal_temperature_c"):
        return True
    note = str(env.get("note") or "")
    return "착색" in note and ("고온" in note or "더위" in note)


def _coloring_verdict(crop, variety, clim, common_env):
    """착색기 기온으로 지역 적합을 본다. (주의, 근거, 심각도, 적온초과℃)

    왜 첫서리가 아니라 착색기 기온인가
      처음에는 '수확기가 첫서리보다 늦으면 주의'로 짰는데, 그 축은 틀렸다. 첫서리는
      일최저기온이 0℃ 이하로 내려간 첫날일 뿐이고 사과·배 성목 내한성은 훨씬 아래다
      (작물표준 사과 extreme_cold_tolerance -30℃). crop_standards의 서리 한계온도표
      flower_frost_threshold_by_stage도 **봄 개화기** 기준이지 가을 수확기가 아니다.
      그 축으로 채점하면 **청송에서 후지가 부적합으로 찍힌다** - 국내 최대 후지 주산지다.

      데이터가 실제로 지지하는 축은 착색 적온이다.
        common_management.environment.coloring_daily_mean_c  12~13℃
        common_management.environment.coloring_night_mean_c  8℃
      사과는 착색기가 서늘해야 색이 든다. 품종별 수확기가 다르므로(쓰가루 8월 /
      후지 10월 하순) 같은 지역에서도 품종에 따라 착색기 기온이 갈린다 - 즉 이 축은
      품종을 실제로 구분한다.

      데이터 자신도 홍로에 대해 "수확기(9월 상중순)에 늦더위가 남아 다른 품종보다
      고온 착색 저해에 취약"이라고 적어 두었다. 이 축은 그 서술과 방향이 같다.

    배는 착색 적온 수치가 데이터에 없다(common_management에 environment 자체가 없다).
    없는 값을 만들지 않으므로 배는 이 판정을 건너뛴다.
    """
    if crop not in REGION_CHECK_CROPS or not clim:
        return [], [], None, None
    lo, hi = _range((common_env or {}).get("coloring_daily_mean_c"))
    if lo is None:
        return [], [], None, None
    win = harvest_window(variety)
    if not win:
        return [], [], None, None
    start = win[0]
    # 착색기 = 수확 시작 직전 30일. 원문이 '착색기'의 길이를 수치로 주지 않아 관행값을
    # 쓰고, 그 사실을 문구에 드러낸다(수확 전 30일).
    begin = season_window.mmdd_add(start, -_COLORING_DAYS)
    mean = _window_mean(clim, begin, start, "avgTa")
    if mean is None:
        return [], [], None, None

    span = f"{begin.replace('-', '/')}~{start.replace('-', '/')}"
    info = (f"착색기(수확 전 30일 {span}) 일평균 {mean}℃ "
            f"— 착색 적온은 {lo}~{hi}℃예요")
    # 적온보다 얼마나 높은가. 정렬에 쓰는 상대 지표다(0이면 적온 안).
    excess = round(max(0.0, mean - hi), 1)

    # 경고는 **데이터가 그 품종에 대해 직접 지적한 경우만** 올린다.
    # 12~13℃라는 밴드는 사실상 만생종 착색 조건이다. 조생종(쓰가루 8월 수확)은 착색기가
    # 7~8월이라 25℃가 나오는 게 정상인데, 밴드를 절대 기준으로 대면 국내에서 실제로
    # 재배되는 조생종이 전부 '착색 불량'으로 찍힌다 - 품종의 특성을 결함으로 표시하는 셈이다.
    # 그래서 기온은 정보로만 보여주고, 고온 착색 저해가 그 품종의 알려진 약점일 때만
    # 주의로 승격한다(홍로: recommended_environment에 착색 최적 25℃와 고온 취약 서술이 있다).
    warn = []
    if excess > 0 and _heat_coloring_sensitive(variety):
        warn.append(f"{info}. 이 품종은 고온에서 착색이 억제되는 편이라 이 지역 기온이면 "
                    f"색이 덜 들 수 있어요")
        return warn, [], "very_warm", excess
    return [], [info], None, excess


def _grade_of(severity, beginner_friendly):
    """조건 매칭 모드의 등급. 점수가 없으므로 라벨로만 구분한다.

    breed.md §6.5를 따라 난이도는 '점수'가 아니라 배지·라벨로만 쓴다.
    착색기 기온이 적온보다 조금 높은 것은 조생종에게 정상이므로 등급을 깎지 않는다.
    데이터가 그 품종의 약점으로 밝힌 고온 착색 저해에 실제로 걸릴 때만 주의로 올린다.
    지역 차이는 등급이 아니라 **정렬**(착색기 기온이 적온에 가까운 순)로 드러낸다.
    """
    if severity == "very_warm":
        return "caution", "착색 주의"
    if beginner_friendly:
        return "good", "초보 추천"
    return "normal", "조건 부합"


def _cultivation_hint(variety):
    """화면의 '작형' 자리에 넣을 문구.

    감자 경로는 여기에 '봄재배' 같은 작형이 들어간다. 조건 모드에는 계산된 작형이
    없으므로, 데이터가 권하는 재배 방식을 대신 넣는다(없으면 빈 문자열).
    """
    for text in variety.get("recommended_season_text") or []:
        if isinstance(text, str) and text.strip():
            return text.strip()
    raw = variety.get("_raw") or {}
    systems = raw.get("recommended_cropping_systems")
    if isinstance(systems, list) and systems:
        first = systems[0]
        if isinstance(first, dict):
            return str(first.get("system") or first.get("name") or "").strip()
        return str(first).strip()
    return ""


def _reasons(variety, region_reasons):
    """카드에 보여줄 근거. 데이터에 적힌 문장을 그대로 쓴다(새로 쓰지 않는다).

    selection_conditions가 1순위지만 **사과는 이 필드가 null이다**(상추·오이·배·감자만
    갖고 있다). 그래서 사과가 실제로 가진 서술로 물러난다 - 용도 → 저장성 → 과실 특징.
    이 폴백이 없으면 착색 정보가 주의로 올라간 품종(홍로)은 근거 줄이 통째로 비어
    카드에 품종명과 경고만 남는다.
    """
    out = list(region_reasons)
    for cond in variety.get("selection_conditions") or []:
        if isinstance(cond, str) and cond.strip():
            out.append(cond.strip())
        elif isinstance(cond, dict):
            text = cond.get("condition") or cond.get("reason")
            if text:
                out.append(str(text).strip())
    if variety.get("beginner_reason"):
        out.append(str(variety["beginner_reason"]).strip())

    if not out:
        uses = [str(u).strip() for u in (variety.get("primary_use") or []) if u]
        if uses:
            out.append("주로 " + " · ".join(uses[:3]) + "에 쓰여요")
        storage = variety.get("storage") or {}
        ability = storage.get("ability")
        if ability:
            cold = storage.get("cold_storage_days_approx")
            out.append(f"저장성 {ability}" + (f" (냉장 약 {cold}일)" if cold else ""))
        if variety.get("headline"):
            out.append(f"과실 특징: {variety['headline']}")
    return out[:4]


def _confidence_notes(variety):
    """추정치임을 밝혀야 하는 값들을 주의로 올린다.

    데이터가 스스로 '불확실'이라고 적어 둔 수치를 화면에서 확정된 사실처럼 보여주면
    안 된다(breed.md §15). 값을 숨기지도 않는다 - 신뢰도를 함께 붙인다.
    """
    out = []
    bloom = variety.get("bloom_to_harvest")
    if bloom and bloom.get("confidence") and not bloom["confidence"].startswith(("확실", "보통")):
        out.append(f"만개 후 {bloom['min']}~{bloom['max']}일로 알려져 있지만 추정치예요"
                   f"({bloom['confidence']})")
    gd = variety.get("growth_days") or {}
    if gd.get("min") and not variety.get("growth_days_scorable"):
        out.append(f"생육기간 {gd['min']}~{gd['max']}일은 추정치예요")
    return out


def recommend(region_name, crop, experience="beginner", years=season_window.DEFAULT_YEARS,
              allow_fetch=True, climatology=None, station=None, matched_region=None):
    """조건 기반 추천. 반환 형태는 cultivar_fit.score_cultivars와 같은 계약을 지킨다.

    인자 순서를 score_cultivars(region_name, crop, ...)와 일부러 같게 맞췄다 -
    호출부(cultivar_api.score_payload)가 두 엔진을 자리만 바꿔 부르기 때문이다.

    기상자료(ASOS)는 **있으면 쓰고 없으면 건너뛴다**. 조건 추천의 본체는 데이터에 적힌
    선택조건이므로, 기상 조회가 실패해도 추천 자체는 나와야 한다. 감자 경로처럼
    no_climate로 끊으면 사과·배·오이·상추가 통째로 화면에서 사라진다.
    """
    payload = cultivar_data.load_crop(crop)
    if not payload:
        return {"error": f"품종 데이터가 아직 없어요: {crop}",
                "available_crops": cultivar_data.available_crops()}
    varieties = payload["varieties"]

    # ── 지역 → 관측소 (로컬 CSV. 실패해도 추천은 계속한다) ──
    region, distance_km = {}, None
    if station is None:
        try:
            from region_mapper import find_nearest_station          # noqa: E402
            m = find_nearest_station(region_name)
            if m.get("status") == "matched":
                station = m["station"]
                matched_region = m.get("matched_region") or {}
                distance_km = m.get("distance_km")
        except Exception as e:                                      # noqa: BLE001
            logger.error("[cultivar_conditions] 관측소 매칭 실패: %s", e)
    if station:
        region = {
            "station_id": station.get("station_id"),
            "station_name": station.get("station_name"),
            "cluster_id": station.get("cluster_id"),
            "cluster_name": station.get("cluster_name"),
            "distance_km": distance_km,
            "sigungu_name": (matched_region or {}).get("sigungu_name"),
        }

    # ── 과수 착색기 기온용 기후자료. 실패는 치명적이지 않다 ──
    # 서리 날짜는 판정에 쓰지 않지만(모듈 도크스트링 참고) 화면이 지역 맥락으로
    # 보여주므로 region_metrics에는 함께 담는다.
    first_fall, frost_note, years_used = None, None, None
    if climatology is None and station and crop in REGION_CHECK_CROPS:
        try:
            climatology = season_window.station_climatology(
                station["station_id"], years=years, allow_fetch=allow_fetch)
        except Exception as e:                                      # noqa: BLE001
            logger.error("[cultivar_conditions] 기상 조회 실패: %s", e)
            climatology = None
    if climatology and climatology.get("status") == "ok":
        frost = climatology.get("frost") or {}
        first_fall = frost.get("first_fall")
        frost_note = frost.get("frost_free_note")
        years_used = frost.get("years_used")
        region.update({
            "frost_free_days": frost.get("frost_free_days"),
            "frost_free_median": frost.get("frost_free_median"),
            "frost_free_note": frost_note,
            "last_spring_frost": frost.get("last_spring"),
            "first_fall_frost": first_fall,
            "years_used": years_used,
        })

    ranking = []
    for v in varieties:
        region_cautions_v, region_reasons, severity, coloring_excess = _coloring_verdict(
            crop, v, climatology if (climatology or {}).get("status") == "ok" else None,
            payload.get("common_environment"))
        # 차단은 재배가 성립하지 않을 때만 쓴다. 조건 모드에서 그 판정을 내릴 근거가
        # 데이터에 없으므로 비워 둔다(_coloring_verdict 주석 참고).
        blockers = []
        cautions = region_cautions_v + _confidence_notes(v)
        grade, grade_label = _grade_of(severity, v.get("beginner_friendly"))

        badges = []
        # beginner_friendly 가 None 이면 '데이터에 그 항목이 없다'는 뜻이다(사과·배).
        # False 와 구분하지 않으면 근거 없이 '시험재배 권장' 배지가 붙는다.
        if v.get("beginner_friendly") is True:
            badges.append("초보자에게 무난")
        elif v.get("beginner_friendly") is False and experience == "beginner":
            badges.append("초보자에겐 소규모 시험재배 권장")
        if payload["unit"] == "품종군":
            badges.append("개별 품종이 아니라 품종군이에요")

        win = harvest_window(v)
        blight = blight_data.blight_info(crop, v["name"])
        pros, cons_list = cultivar_reasons.build(
            v, region_pros=region_reasons, region_cons=region_cautions_v,
            blight=blight, experience=experience)
        ranking.append({
            "cultivar": v["name"],
            "aliases": v["aliases"],
            "maturity": v["maturity"],
            "category": v["category"],
            # 점수를 내지 않는다. 프런트는 score가 숫자가 아니면 '-'로 표시한다.
            "score": None,
            "grade": grade, "grade_label": grade_label,
            "cultivation_type": _cultivation_hint(v),
            # 파종일 창이 없다(과수는 파종이 없고, 오이·상추는 작형이 데이터로만 있다).
            # 프런트는 from/to가 없으면 이 줄을 렌더하지 않는다.
            "planting_window": {},
            "harvest_window": ({"from": win[0], "to": win[1]} if win else {}),
            "scoring_mode": cultivar_data.SCORING_CONDITIONS,
            # 지역 적합 신호(None / warm / very_warm = 착색기 기온). 정렬·등급이 쓴다.
            "region_fit": severity,
            # 착색 적온을 몇 ℃ 넘었나(정렬용). None이면 판정 불가.
            "coloring_excess_c": coloring_excess,
            "blockers": blockers,
            "cautions": cautions,
            "variety_warnings": (v.get("key_warnings") or [])[:3],
            "badges": badges,
            # pros/cons 가 화면의 '추천 이유 / 고려할 점'이다. reasons·cautions 는
            # 챗봇 축약과 기존 소비자를 위해 남겨 둔다(같은 내용의 평문 목록).
            "pros": pros,
            "cons": cons_list,
            "late_blight": blight,
            "reasons": _reasons(v, region_reasons),
            "beginner_friendly": v.get("beginner_friendly"),
            "beginner_reason": v.get("beginner_reason"),
            "primary_use": v.get("primary_use"),
            "headline": v.get("headline"),
            "bloom_to_harvest": v.get("bloom_to_harvest"),
            "report": v.get("report"),
        })

    # 점수가 없으니 정렬이 곧 추천 순서다.
    # 지역에 맞는 것 먼저(서리와 겹치지 않는 순) → 초보 적합 → 수확기 이른 순.
    # 착색 적온에 가까운 순 → 데이터가 고온 취약이라 지적한 품종은 뒤로 → 초보 적합
    # → 수확기 이른 순. 지역이 바뀌면 착색기 기온이 바뀌므로 순서도 실제로 바뀐다.
    ranking.sort(key=lambda r: (r.get("region_fit") == "very_warm",
                                r.get("coloring_excess_c") if r.get("coloring_excess_c") is not None else 99,
                                not r["beginner_friendly"],
                                (r.get("harvest_window") or {}).get("from") or "99-99"))

    cautions = list(cultivar_data.dataset_cautions(crop))
    # 정렬 기준의 한계를 숨기지 않는다. 착색 적온 12~13℃는 사실상 만생종 기준이라
    # 이 순서는 늦게 따는 품종이 앞서는 경향이 있다. 지역은 순서보다 '몇 ℃ 초과'라는
    # 수치와 품종별 주의로 드러난다. 이걸 밝히지 않으면 사용자가 1위를 '이 지역 최적'으로
    # 읽는데, 실제로는 '가장 늦게 따는 품종'일 뿐인 경우가 생긴다.
    if crop == "사과" and any(r.get("coloring_excess_c") is not None for r in ranking):
        cautions.insert(0, "순서는 착색 적온(12~13℃)에 가까운 순이에요. 그 기준이 만생종에 맞춰져 "
                           "있어 늦게 따는 품종이 앞서는 경향이 있으니, 출하 시기와 판매 계획도 "
                           "함께 보고 고르세요")
    if crop in REGION_CHECK_CROPS and not first_fall:
        cautions.insert(0, "이 지역 서리 자료를 확보하지 못해 수확기와 서리를 맞춰보지 못했어요")
    if crop not in REGION_CHECK_CROPS:
        cautions.insert(0, "이 작물은 어느 작형(재배 시기)을 고르는지가 지역보다 먼저예요. "
                           "아래 조건을 먼저 보고 작형을 정하세요")
    if distance_km and distance_km >= 10 and region.get("station_name"):
        cautions.insert(0, f"기준 관측소({region['station_name']})가 {distance_km}km 떨어져 있어요")

    return {
        "status": "matched",
        "crop": crop,
        "region": region_name,
        "experience": experience,
        "scoring_mode": cultivar_data.SCORING_CONDITIONS,
        "unit": payload["unit"],
        "region_metrics": region,
        "soil_readings": {},
        "ranking": ranking,
        "skipped": [],
        "selection_guide": payload.get("selection_guide") or [],
        "reliability": "정상" if (first_fall or crop not in REGION_CHECK_CROPS) else "주의",
        "reliability_reason": (None if (first_fall or crop not in REGION_CHECK_CROPS)
                              else "서리 자료 없음"),
        "cautions": cautions,
        "data_sources": {
            "품종": payload.get("source_file"),
            "기상": (f"기상청 ASOS 일자료 {years_used}년 평년(관측소 {region.get('station_name')})"
                     if first_fall else "미사용(조건 기반 추천)"),
        },
    }
