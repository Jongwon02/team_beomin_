# -*- coding: utf-8 -*-
"""품종 API 페이로드 조립 (breed.md §7).

crop_score_server(:8002)의 HTTP 핸들러가 얇게 유지되도록 응답 만들기만 여기서 한다.
계산은 backend/scoring/cultivar_fit.py, 데이터는 backend/scoring/cultivar_data.py.

캐시: 품종 점수는 ASOS 일자료(불변, 디스크 캐시)와 흙토람(연 1회 갱신)만 쓰므로
사실상 정적이다. 그래도 흙토람 호출을 반복하지 않도록 프로세스 내에서 30분 잡아둔다.
"""

import logging
import re
import time
from pathlib import Path

import cultivar_conditions
import cultivar_data
import cultivar_fit
import cultivar_fruit_fit
import cultivar_season_fit

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
SCORE_TTL = 30 * 60
_score_cache = {}       # (crop, region, experience) -> (ts, payload)
_report_cache = {}      # path -> (mtime, [{"no","title","body"}])


def cultivars_payload(crop):
    """품종 목록·특성(지역 무관). breed.md §7.1"""
    payload = cultivar_data.load_crop(crop)
    if not payload:
        return {"error": f"품종 데이터가 아직 없어요: {crop}",
                "available_crops": cultivar_data.available_crops()}

    out = []
    for v in payload["varieties"]:
        gd = v["growth_days"]
        days = (f"{gd['min']}~{gd['max']}일" if gd.get("max") else
                (f"{gd['min']}일 이상" if gd.get("min") else None))
        # 과수는 파종~수확 일수가 없다. 대신 만개후일수를 쓰되 그것이 만개 기준임을
        # 문구에 박아 둔다 - '188일'만 보이면 파종 후 188일로 읽힌다.
        bloom = v.get("bloom_to_harvest")
        if not days and bloom:
            days = f"만개 후 {bloom['min']}~{bloom['max']}일"
            if bloom.get("confidence") and not bloom["confidence"].startswith(("확실", "보통")):
                days += " (추정)"
        out.append({
            "name": v["name"], "aliases": v["aliases"], "maturity": v["maturity"],
            "growth_days": days, "category": v["category"],
            "primary_use": v["primary_use"][:3],
            "headline": v["headline"],
            "beginner_friendly": v["beginner_friendly"],
            "seasons": v["seasons"], "seasons_excluded": v["seasons_excluded"],
            "key_warnings": v["key_warnings"][:3],
            "has_report": bool(v["report"]),
        })
    return {
        "crop": crop, "count": len(out), "cultivars": out,
        "unit": payload["unit"],
        "scoring_mode": payload["scoring_mode"],
        "common_management": payload["common_management"],
        "selection_guide": payload.get("selection_guide") or [],
        "cautions": cultivar_data.dataset_cautions(crop),
        "source": payload["source_file"],
    }


def score_payload(crop, region, experience="beginner", crop_score=None):
    """지역별 품종 순위. breed.md §7.2

    crop_score를 주면(서버 캐시에 이미 있는 작물 점수) 응답에 함께 실어 화면이
    "감자 84점 → 그중 추백 91점"으로 이을 수 있게 한다. 없으면 새로 계산하지 않는다
    (작물 점수는 공공 API 여러 개를 타서 느리다).
    """
    key = (crop, region, experience)
    now = time.time()
    hit = _score_cache.get(key)
    if hit and now - hit[0] < SCORE_TTL:
        payload = hit[1]
    else:
        # 작물에 따라 엔진이 갈린다(cultivar_data.CROP_SCORING_MODE).
        #   climate       감자    - 파종일을 훑는 기후 채점(cultivar_fit)
        #   climate_fruit 사과·배 - 수확·착색기를 앵커로 한 과수 채점(cultivar_fruit_fit)
        #   season        상추·오이 - 품종이 아니라 **작형**을 채점(cultivar_season_fit)
        #   conditions    그 외    - 데이터에 적힌 선택조건 기반(cultivar_conditions)
        # 4작물을 감자 모델에 태우면 만개후일수가 생육일수로 읽혀 "재배 불가"가 찍히거나
        # 품종이 전부 동점이 된다. 자세한 근거는 각 모듈 도크스트링.
        mode = cultivar_data.scoring_mode(crop)
        if mode == cultivar_data.SCORING_CLIMATE:
            payload = cultivar_fit.score_cultivars(region, crop, experience=experience)
        elif mode == cultivar_data.SCORING_CLIMATE_FRUIT:
            payload = cultivar_fruit_fit.score_fruit_cultivars(region, crop, experience=experience)
        elif mode == cultivar_data.SCORING_SEASON:
            payload = cultivar_season_fit.score_seasons(region, crop, experience=experience)
        else:
            payload = cultivar_conditions.recommend(region, crop, experience=experience)
        if payload.get("status") == "matched":
            _score_cache[key] = (now, payload)

    if crop_score and payload.get("status") == "matched":
        payload = dict(payload, crop_score=crop_score)
    return payload


def profile_payload(crop, name, topic=None):
    """품종 1개 상세(구조화 필드). breed.md §7.3"""
    return cultivar_fit.cultivar_profile(crop, name, topic=topic)


# ── L1 리포트(사람이 쓴 마크다운) 제공 ──────────────────────────────────────
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)


def _split_sections(text):
    """'## ' 제목 단위로 자른다. breed.md §3.1의 섹션 골격이 곧 이 경계다."""
    marks = list(_SECTION_RE.finditer(text))
    sections = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        title = m.group(1).strip()
        no = title.split(".")[0].strip() if title[:1].isdigit() else str(i + 1)
        sections.append({"no": no, "title": title, "body": text[m.end():end].strip()})
    return sections


def _load_report(rel_path):
    path = BASE_DIR / rel_path
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    hit = _report_cache.get(rel_path)
    if hit and hit[0] == mtime:
        return hit[1]
    sections = _split_sections(path.read_text(encoding="utf-8"))
    _report_cache[rel_path] = (mtime, sections)
    return sections


def report_payload(crop, name, section=None):
    """품종 리포트. section 없으면 목차 + 각 섹션 첫 문단만(응답 폭주 방지)."""
    v = cultivar_data.find_variety(crop, name)
    if not v:
        return {"error": f"품종 데이터가 아직 없어요: {crop} '{name}'",
                "available": cultivar_data.variety_names(crop)}
    if not v.get("report"):
        return {"error": f"'{v['name']}'은 아직 리포트가 없어요(품종 데이터만 있습니다).",
                "cultivar": v["name"], "crop": crop, "has_report": False}

    sections = _load_report(v["report"])
    if sections is None:
        return {"error": f"리포트 파일을 찾을 수 없어요: {v['report']}"}

    if section == "all":
        body = sections
    elif section:
        body = [s for s in sections if s["no"] == str(section) or s["title"].startswith(str(section))]
        if not body:
            return {"error": f"{section}번 섹션이 없어요",
                    "toc": [{"no": s["no"], "title": s["title"]} for s in sections]}
    else:
        body = [{"no": s["no"], "title": s["title"],
                 "excerpt": s["body"].split("\n\n")[0][:300]} for s in sections]

    return {
        "crop": crop, "cultivar": v["name"], "report_path": v["report"],
        "toc": [{"no": s["no"], "title": s["title"]} for s in sections],
        "sections": body,
        "note": "이 리포트는 사람이 작성·검수한 원문입니다. 수치는 품종 데이터와 같은 값을 씁니다.",
    }
