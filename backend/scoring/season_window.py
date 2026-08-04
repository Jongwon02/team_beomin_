# -*- coding: utf-8 -*-
"""지역(관측소) 단위 '작기 지표' 산출 — 품종 채점의 기상 근거 (breed.md §5.2).

왜 이 모듈이 필요한가
  작물 점수(live_scoring)는 "지금 이 지역이 이 작물에 맞는가"를 본다. 그런데 품종은
  **작기(파종~수확 구간)**에서 갈린다. 추백(80~90일)과 자영(110일 이상)의 차이는
  연평균기온이 아니라 "그 구간에 서리가 걸리는가, 비대기가 더운가"에서 나온다.
  그래서 이 모듈은 ASOS 일자료 10년치로 다음을 만든다.

    · 무상기간          — 봄 마지막 서리일 ~ 가을 첫 서리일 (연도별 + 보수적 대표값)
    · 작기 구간 통계    — 파종일과 생육일수를 주면 그 구간의 평균기온·고온일수·
                          강수·집중강수일수·비대기(수확 전 30일) 평균기온

설계상 중요한 선택 3가지
  1. **1년 = 1회 호출.** asos.get_daily_records의 numOfRows가 400이라 1/1~12/31(365일)이
     한 페이지에 들어간다. 관측소당 10회 호출로 10년치가 끝난다.
  2. **원본 일자료를 디스크에 캐시.** 과거 실측은 불변이므로 만료가 없다
     (data/cache/asos_daily/<지점>_<연도>.json). 두 번째 조회부터는 네트워크 0회.
  3. **대표 무상기간은 평균이 아니라 짧은 쪽 20퍼센타일.** 평균을 쓰면 10년 중 절반은
     그보다 짧다 — 만생종 판정에서 "가능하다"고 잘못 말하게 된다. 농사는 실패가
     비대칭이라 보수적으로 잡는다(연도별 값도 함께 돌려주어 화면에서 밝힐 수 있게).

이 모듈은 데이터만 만든다. 품종별 판정은 cultivar_fit.py가 한다.
"""

import json
import logging
import statistics
from datetime import date, timedelta
from pathlib import Path

import asos                                              # noqa: E402  (backend/api, sys.path 등록 전제)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = BASE_DIR / "data" / "cache" / "asos_daily"

DEFAULT_YEARS = 10
MIN_USABLE_YEARS = 3          # 이보다 적으면 대표값을 만들지 않는다(무상기간은 연변동이 크다)

FROST_TEMP_C = 0.0            # 일 최저기온 0℃ 이하를 서리일로 본다(기상학적 무상기간 관례)
SPRING_LAST_MONTH = 6         # 봄 서리 탐색 구간: 1~6월
FALL_FIRST_MONTH = 8          # 가을 서리 탐색 구간: 8~12월

BULKING_DAYS = 30             # 수확 직전 30일 = 괴경 비대 후기(품질이 갈리는 구간)
EMERGENCE_DAYS = 20           # 파종 후 약 20일은 싹이 땅속 - 이 기간 서리는 위험으로 세지 않는다
HEAVY_RAIN_MM = 50.0          # 집중강수일 기준


# ═══════════════════════════════════════════════════════════════
# 1. 원본 일자료 (ASOS + 영구 캐시)
# ═══════════════════════════════════════════════════════════════

def _cache_path(station_id, year):
    return CACHE_DIR / f"{station_id}_{year}.json"


def station_year_records(station_id, year, allow_fetch=True):
    """관측소·연도의 일자료 [{date(YYYYMMDD), avgTa, minTa, maxTa, sumRn, sumSsHr}, ...].

    캐시에 있으면 그걸 쓴다. 없고 allow_fetch면 ASOS를 한 번 호출해 저장한다.
    실패하면 None (호출부가 그 연도를 건너뛴다).
    """
    path = _cache_path(station_id, year)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:                                    # noqa: BLE001
            logger.warning("[season_window] 캐시 파일 손상, 다시 받는다 (%s): %s", path.name, e)

    if not allow_fetch:
        return None

    records = asos.get_daily_records(station_id, f"{year}0101", f"{year}1231")
    if not records:
        logger.warning("[season_window] ASOS 조회 실패/빈 응답 (지점=%s, %s년)", station_id, year)
        return None

    # 기온이 통째로 결측인 응답(관측소 미가동 등)은 캐시하지 않는다 - 다음에 다시 시도.
    if not any(r.get("minTa") is not None for r in records):
        logger.warning("[season_window] 기온 전량 결측 (지점=%s, %s년) - 캐시하지 않음", station_id, year)
        return None

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    except Exception as e:                                        # noqa: BLE001
        logger.warning("[season_window] 캐시 저장 실패(%s): %s", path.name, e)
    return records


# ═══════════════════════════════════════════════════════════════
# 2. 서리일 · 무상기간
# ═══════════════════════════════════════════════════════════════

def _to_date(yyyymmdd):
    return date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))


def _frost_dates(records):
    """한 해 일자료 -> (봄 마지막 서리일, 가을 첫 서리일) datetime.date. 없으면 None.

    봄 서리가 아예 없는 해(제주 등)는 (None, ...)이 되고, 그런 지역의 무상기간은
    '사실상 연중'이므로 호출부에서 별도 처리한다.
    """
    spring, fall = None, None
    for r in records:
        t = r.get("minTa")
        if t is None or t > FROST_TEMP_C:
            continue
        d = _to_date(r["date"])
        if d.month <= SPRING_LAST_MONTH:
            if spring is None or d > spring:
                spring = d
        elif d.month >= FALL_FIRST_MONTH:
            if fall is None or d < fall:
                fall = d
    return spring, fall


def _percentile(values, q):
    """q(0~1) 분위값. statistics.quantiles는 표본이 적으면 예외라 직접 계산한다."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _mmdd(d):
    return None if d is None else d.strftime("%m-%d")


# ═══════════════════════════════════════════════════════════════
# 3. 기후값(여러 해 묶음)
# ═══════════════════════════════════════════════════════════════

def station_climatology(station_id, years=DEFAULT_YEARS, today=None, allow_fetch=True):
    """관측소의 최근 years개 '완결된 연도' 일자료와 서리·무상기간 통계.

    반환:
      {"status": "ok"|"insufficient",
       "station_id", "years": [연도...], "missing_years": [...],
       "by_year": {연도: [일자료...]},
       "frost": {"last_spring": "05-11", "first_fall": "09-16",         # 보수적 대표값
                 "frost_free_days": 128,                                # p20(짧은 쪽)
                 "frost_free_median": 141, "frost_free_min": 118, "frost_free_max": 158,
                 "by_year": {연도: {"last_spring","first_fall","frost_free_days"}},
                 "frost_free_note": "..."},
       }
    올해는 아직 서리 정보가 안 끝났으므로 항상 '작년까지'만 본다.
    """
    today = today or date.today()
    last_complete = today.year - 1
    wanted = list(range(last_complete - years + 1, last_complete + 1))

    by_year, missing = {}, []
    for y in wanted:
        recs = station_year_records(station_id, y, allow_fetch=allow_fetch)
        if recs:
            by_year[y] = recs
        else:
            missing.append(y)

    frost_by_year, free_days = {}, []
    for y, recs in by_year.items():
        spring, fall = _frost_dates(recs)
        entry = {"last_spring": _mmdd(spring), "first_fall": _mmdd(fall), "frost_free_days": None}
        if spring and fall:
            entry["frost_free_days"] = (fall - spring).days
        elif fall and not spring:
            # 봄 서리가 없던 해: 1/1부터 무상으로 본다(온난 지역). 과대평가를 피해
            # 무상기간 대표값 계산에는 넣되 근거를 남긴다.
            entry["frost_free_days"] = (fall - date(y, 1, 1)).days
            entry["note"] = "봄 서리 없음(1월 1일 기준)"
        elif spring and not fall:
            entry["frost_free_days"] = (date(y, 12, 31) - spring).days
            entry["note"] = "가을 서리 없음(12월 31일 기준)"
        else:
            entry["frost_free_days"] = 365
            entry["note"] = "연중 서리 없음"
        frost_by_year[y] = entry
        if entry["frost_free_days"] is not None:
            free_days.append(entry["frost_free_days"])

    if len(free_days) < MIN_USABLE_YEARS:
        return {
            "status": "insufficient", "station_id": station_id,
            "years": sorted(by_year), "missing_years": missing, "by_year": by_year,
            "frost": None,
            "message": f"ASOS 일자료를 {len(free_days)}년치만 확보해 무상기간 대표값을 만들 수 없습니다.",
        }

    # 서리일 대표값도 보수적으로: 봄 서리는 '늦은 쪽'(p80), 가을 서리는 '이른 쪽'(p20).
    # ⚠️ 통산일수는 반드시 '평년(윤년 아님)' 기준으로 환산한다. 실제 연도의 tm_yday를
    #    쓰면 3월 이후 날짜가 윤년에만 +1이 되어, 표본에 섞인 윤년 개수에 따라 대표
    #    서리일이 하루씩 밀린다(합성 기상으로 매년 4/20에 서리를 줬는데 4/21이 나왔다).
    ref_year = 2001  # 윤년이 아닌 해로 고정
    def _mmdd_to_doy(mmdd):
        month, day = int(mmdd[:2]), int(mmdd[3:5])
        if (month, day) == (2, 29):                 # 윤일 서리는 2/28로 본다(평년에 없는 날)
            day = 28
        return date(ref_year, month, day).timetuple().tm_yday

    spring_doys = [_mmdd_to_doy(frost_by_year[y]["last_spring"])
                   for y in frost_by_year if frost_by_year[y]["last_spring"]]
    fall_doys = [_mmdd_to_doy(frost_by_year[y]["first_fall"])
                 for y in frost_by_year if frost_by_year[y]["first_fall"]]

    def _doy_to_mmdd(doy):
        if doy is None:
            return None
        return (date(ref_year, 1, 1) + timedelta(days=int(round(doy)) - 1)).strftime("%m-%d")

    return {
        "status": "ok",
        "station_id": station_id,
        "years": sorted(by_year),
        "missing_years": missing,
        "by_year": by_year,
        "frost": {
            "last_spring": _doy_to_mmdd(_percentile(spring_doys, 0.8)) if spring_doys else None,
            "first_fall": _doy_to_mmdd(_percentile(fall_doys, 0.2)) if fall_doys else None,
            "frost_free_days": int(round(_percentile(free_days, 0.2))),
            "frost_free_median": int(round(statistics.median(free_days))),
            "frost_free_min": min(free_days),
            "frost_free_max": max(free_days),
            "years_used": len(free_days),
            "by_year": frost_by_year,
            "frost_free_note": (
                f"최근 {len(free_days)}년 중 짧은 쪽 20퍼센타일 값입니다 "
                f"(중앙값 {int(round(statistics.median(free_days)))}일, "
                f"가장 짧은 해 {min(free_days)}일)."
            ),
        },
    }


# ═══════════════════════════════════════════════════════════════
# 4. 작기 구간 통계
# ═══════════════════════════════════════════════════════════════

def _year_index(records):
    """[{date, ...}] -> {date객체: 레코드}"""
    out = {}
    for r in records:
        try:
            out[_to_date(r["date"])] = r
        except Exception:                                         # noqa: BLE001
            continue
    return out


def window_metrics(clim, plant_mmdd, growth_days, hot_threshold=25.0,
                   bulking_days=BULKING_DAYS, hot_threshold_severe=30.0):
    """파종일(MM-DD)과 생육일수를 주면 그 작기 구간의 기후 통계를 연도 평균으로 낸다.

    반환 (연도 평균):
      {"plant": "05-21", "harvest": "09-08", "days": 110, "years_used": 9,
       "window_mean_temp": 16.4, "bulking_mean_temp": 15.1, "late_delta": -1.3,
       "hot_days": 3.2,            # 비대기 maxTa > hot_threshold 일수
       "hot_days_severe": 0.4,     # 비대기 maxTa > 30℃ 일수
       "window_rain_mm": 812.5, "heavy_rain_days": 4.1,
       "frost_days_after_emergence": 0.2,
       "truncated_years": 1}       # 수확일이 12/31을 넘어 잘린 해 수
    쓸 수 있는 해가 없으면 None.
    """
    if not clim or clim.get("status") != "ok" or not growth_days:
        return None

    month, day = int(plant_mmdd[:2]), int(plant_mmdd[3:5])
    per_year = []
    truncated = 0

    for y, records in clim["by_year"].items():
        idx = _year_index(records)
        try:
            plant = date(y, month, day)
        except ValueError:                                        # 2/29 같은 입력 방어
            continue
        harvest = plant + timedelta(days=int(growth_days))
        if harvest.year != y:
            truncated += 1
            harvest = date(y, 12, 31)
        if (harvest - plant).days < 30:
            continue

        span = [idx[plant + timedelta(days=i)] for i in range((harvest - plant).days + 1)
                if (plant + timedelta(days=i)) in idx]
        if len(span) < 30:
            continue

        temps = [r["avgTa"] for r in span if r.get("avgTa") is not None]
        if not temps:
            continue

        bulking = span[-bulking_days:] if len(span) > bulking_days else span
        b_temps = [r["avgTa"] for r in bulking if r.get("avgTa") is not None]
        b_max = [r["maxTa"] for r in bulking if r.get("maxTa") is not None]
        after_emergence = span[EMERGENCE_DAYS:]
        # 파종~출현 구간(약 20일). 씨감자가 땅속에 있는 시기로, 이때가 너무 더우면
        # 부패·결주가 나고 너무 추우면 출현이 지연된다 - 작기 성패가 여기서 갈린다.
        emergence = span[:EMERGENCE_DAYS]
        e_temps = [r["avgTa"] for r in emergence if r.get("avgTa") is not None]

        per_year.append({
            "emergence_mean_temp": (sum(e_temps) / len(e_temps)) if e_temps else None,
            "window_mean_temp": sum(temps) / len(temps),
            "bulking_mean_temp": (sum(b_temps) / len(b_temps)) if b_temps else None,
            "hot_days": sum(1 for t in b_max if t > hot_threshold),
            "hot_days_severe": sum(1 for t in b_max if t > hot_threshold_severe),
            "window_rain_mm": sum(r.get("sumRn") or 0.0 for r in span),
            "heavy_rain_days": sum(1 for r in span if (r.get("sumRn") or 0.0) >= HEAVY_RAIN_MM),
            "frost_days_after_emergence": sum(
                1 for r in after_emergence
                if r.get("minTa") is not None and r["minTa"] <= FROST_TEMP_C
            ),
        })

    if not per_year:
        return None

    def _avg(key):
        vals = [p[key] for p in per_year if p[key] is not None]
        return (sum(vals) / len(vals)) if vals else None

    window_mean = _avg("window_mean_temp")
    bulking_mean = _avg("bulking_mean_temp")
    emergence_mean = _avg("emergence_mean_temp")
    plant_date = date(2001, month, day)
    harvest_date = plant_date + timedelta(days=int(growth_days))

    return {
        "plant": plant_mmdd,
        "harvest": harvest_date.strftime("%m-%d"),
        "days": int(growth_days),
        "years_used": len(per_year),
        "emergence_mean_temp": round(emergence_mean, 1) if emergence_mean is not None else None,
        "window_mean_temp": round(window_mean, 1) if window_mean is not None else None,
        "bulking_mean_temp": round(bulking_mean, 1) if bulking_mean is not None else None,
        "late_delta": (round(bulking_mean - window_mean, 1)
                       if (bulking_mean is not None and window_mean is not None) else None),
        "hot_days": round(_avg("hot_days") or 0.0, 1),
        "hot_days_severe": round(_avg("hot_days_severe") or 0.0, 1),
        "window_rain_mm": round(_avg("window_rain_mm") or 0.0, 1),
        "heavy_rain_days": round(_avg("heavy_rain_days") or 0.0, 1),
        "frost_days_after_emergence": round(_avg("frost_days_after_emergence") or 0.0, 1),
        "truncated_years": truncated,
    }


def mmdd_add(mmdd, days):
    """'05-11' + 21일 -> '06-01' (윤년 무시용 고정 기준연도)."""
    d = date(2001, int(mmdd[:2]), int(mmdd[3:5])) + timedelta(days=days)
    return d.strftime("%m-%d")


def mmdd_diff(a, b):
    """a - b (일수). 같은 해 기준."""
    da = date(2001, int(a[:2]), int(a[3:5]))
    db = date(2001, int(b[:2]), int(b[3:5]))
    return (da - db).days
