"""사용자가 입력한 행정구역명(시군구/읍면동)을 가장 가까운 기상 관측소에 매핑한다."""

import functools
import json
import logging
import math
from pathlib import Path

from crop_station_registry import CROP_STATION_REGISTRY, DISTANCE_WARNING_THRESHOLD_KM

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]  # farm-guide/
SIGUNGU_COORDS_PATH = BASE_DIR / "data" / "raw" / "sigungu_coordinates.json"
STATION_MAP_PATH = BASE_DIR / "data" / "processed" / "region_cluster_map.json"

# 2023~2024년 지자체명 변경 대응: 새 명칭으로 입력해도 원본 데이터(구 명칭)와 매칭되도록 정규화한다.
SIDO_RENAME_MAP = {
    "강원특별자치도": "강원도",
    "전북특별자치도": "전라북도",
}

# 제주는 위 두 경우와 반대 방향(구->신)이다 - sigungu_coordinates.json이 이미
# "제주특별자치도"로 갱신되어 있어서, 프론트엔드/사용자가 예전 이름 "제주도"로
# 입력했을 때만 신 명칭으로 올려준다. 이 매핑이 없으면 "제주도 제주시"처럼 시도+시군구
# 2어절 입력은 시도 어간 비교(_sido_stem)가 둘 다 "제주"로 줄어들어 우연히 매칭되지만,
# "제주도 제주시 <읍면동>" 3어절 입력은 emd_exact/sigungu_exact 단계에서 전혀
# 매칭되지 않아 ambiguous로 빠진다 (제주 전역 추천이 안 뜨는 버그의 원인).
SIDO_OLD_TO_NEW_MAP = {
    "제주도": "제주특별자치도",
}

# 개별 시군구 단위 행정구역 개편(시 승격/광역시 편입/개칭) 대응. SIDO_RENAME_MAP과
# 정반대 방향(구 명칭 -> 신 명칭)이다 - sigungu_coordinates.json은 이미 최신 명칭으로
# 갱신해뒀으므로, 사용자가 예전 이름으로 입력했을 때만 신 명칭으로 올려준다.
# 군위군처럼 시도 자체가 바뀌는 경우도 있어(경상북도->대구광역시) "시도 시군구"
# 전체를 키로 둔다 - SIDO_RENAME_MAP처럼 시도만 따로 치환할 수 없다(경상북도의 다른
# 시군구까지 대구광역시로 잘못 치환되면 안 되므로).
SIGUNGU_RENAME_MAP = {
    "경기도 여주군": "경기도 여주시",  # 2013 시 승격
    "경상북도 군위군": "대구광역시 군위군",  # 2023 대구광역시 편입
    "인천광역시 남구": "인천광역시 미추홀구",  # 2018 개칭
}

# 시/도명 약칭 인식용 (예: "강원 평창" -> 시도="강원"). 축약형이 원래 명칭의 접미사만 뗀 형태가
# 아닌 경우(충청북도->충북 등)만 별도로 등록한다.
SIDO_COMPOUND_ALIASES = {
    "충청북도": "충북",
    "충청남도": "충남",
    "전라북도": "전북",
    "전라남도": "전남",
    "경상북도": "경북",
    "경상남도": "경남",
}
SIDO_SUFFIXES = ["특별자치시", "특별자치도", "광역시", "특별시", "도"]
SIGUNGU_SUFFIXES = ["시", "군", "구"]


def _normalize_whitespace(s):
    return " ".join(s.split())


def _canonicalize_sido_prefix(s):
    for new_name, old_name in SIDO_RENAME_MAP.items():
        if s.startswith(new_name):
            return old_name + s[len(new_name):]
    for old_name, new_name in SIDO_OLD_TO_NEW_MAP.items():
        if s.startswith(old_name):
            return new_name + s[len(old_name):]
    return s


def _canonicalize_sigungu_rename(s):
    """SIGUNGU_RENAME_MAP 키를 시도 약칭까지 인식해서 적용한다("인천 남구"처럼
    "인천광역시"를 "인천"으로 줄인 입력도 매칭돼야 하므로, 시도 부분은 stem
    비교(_sido_stem)로 유연하게 처리한다)."""
    tokens = s.split(" ")
    if len(tokens) < 2:
        return s
    sido_part, rest = tokens[0], " ".join(tokens[1:])
    for old_name, new_name in SIGUNGU_RENAME_MAP.items():
        old_sido, old_sigungu = old_name.split(" ", 1)
        if _sido_stem(sido_part) != _sido_stem(old_sido):
            continue
        if rest == old_sigungu or rest.startswith(old_sigungu + " "):
            return new_name + rest[len(old_sigungu):]
    return s


def _canonicalize_region_name(s):
    """시도명 신/구 치환 + 개별 시군구 개편(승격/편입/개칭) 치환을 순서대로 적용한다."""
    return _canonicalize_sigungu_rename(_canonicalize_sido_prefix(s))


def _sido_only_part(sigungu_name):
    """'서울특별시 종로구' -> '서울특별시'"""
    return sigungu_name.split(" ")[0]


def _sigungu_only_part(sigungu_name):
    """'서울특별시 종로구' -> '종로구'"""
    parts = sigungu_name.split(" ")
    return parts[-1] if parts else sigungu_name


def _sido_stem(name):
    """'강원도' -> '강원', '충청북도' -> '충북' 같은 축약형 비교용 어간을 만든다."""
    if name in SIDO_COMPOUND_ALIASES:
        return SIDO_COMPOUND_ALIASES[name]
    for suf in SIDO_SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf):
            return name[: -len(suf)]
    return name


def _sigungu_stem(name):
    """'평창군' -> '평창', '중구' -> '중' 같은 접미사 제거형을 만든다."""
    for suf in SIGUNGU_SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf):
            return name[: -len(suf)]
    return name


def _derive_sigungu_code(emd_code):
    """8자리 emd_code(시도2+시군구3+읍면동3)에서 앞 5자리(시군구 코드)를 뽑아낸다."""
    code = str(emd_code)
    return code[:5] if len(code) >= 5 else None


def haversine_distance(lat1, lon1, lat2, lon2):
    """두 좌표 간 거리를 km 단위로 반환한다 (Haversine 공식)."""
    r = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _load_json(path):
    if not path.exists():
        logger.warning("[region_mapper] %s 파일이 없습니다.", path)
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@functools.lru_cache(maxsize=4)
def _load_sigungu_records(path=SIGUNGU_COORDS_PATH):
    # 이 파일이 1MB가 넘어서, 매 요청마다 다시 읽고 5천여 건을 정규화하면 예보 요청이
    # 여러 지역에서 동시에 들어올 때 GIL 경합으로 전체가 느려진다. 내용이 바뀌지 않는
    # 정적 데이터라 프로세스 생애주기 동안 한 번만 읽고 캐시한다.
    raw = _load_json(path)
    records = []
    for r in raw:
        records.append(
            {
                "emd_code": r["emd_code"],
                "full_name": _canonicalize_region_name(_normalize_whitespace(r["full_name"])),
                "sigungu_name": _canonicalize_region_name(_normalize_whitespace(r["sigungu_name"])),
                "emd_name": _normalize_whitespace(r["emd_name"]),
                "lat": r["lat"],
                "lon": r["lon"],
            }
        )
    return records


@functools.lru_cache(maxsize=4)
def _load_stations(path=STATION_MAP_PATH):
    return _load_json(path)


def _group_first_by_sigungu(records):
    """sigungu_name별 대표(첫 등장) 레코드 dict를 만든다."""
    groups = {}
    for r in records:
        groups.setdefault(r["sigungu_name"], []).append(r)
    return groups


def _candidates_from_groups(groups):
    candidates = []
    for sigungu_name, members in groups.items():
        rep = members[0]
        candidates.append(
            {"full_name": sigungu_name, "region_code": _derive_sigungu_code(rep["emd_code"])}
        )
    return candidates


def _match_region(raw_input, records):
    """행정구역명을 records(읍면동 단위)에 대해 단계적으로 매칭한다.

    반환: {"stage": int, "stage_name": str, "status": "matched"|"ambiguous"|"not_found",
           "matches": [record, ...]}
    matched일 때 matches는 길이 1, ambiguous일 때는 서로 다른 시군구를 대표하는 레코드들.
    """
    normalized = _canonicalize_region_name(_normalize_whitespace(raw_input))
    compact = normalized.replace(" ", "")
    tokens = normalized.split(" ")

    # 1단계: 읍면동까지 포함한 전체 명칭 정확 일치 (가장 정밀한 좌표)
    full_matches = [r for r in records if r["full_name"] == normalized]
    if len(full_matches) == 1:
        logger.info("[region_mapper] '%s' -> 1단계(전체명 정확일치)", raw_input)
        return {
            "stage": 1,
            "stage_name": "emd_exact",
            "status": "matched",
            "matches": full_matches,
            "precision": "emd",
        }

    # 2단계: "시도 시군구" 정확 일치 -> 그 시군구의 대표(첫 읍면동) 좌표 사용
    sigungu_exact = [r for r in records if r["sigungu_name"] == normalized]
    if sigungu_exact:
        logger.info("[region_mapper] '%s' -> 2단계(시군구명+시도 정확일치)", raw_input)
        return {
            "stage": 2,
            "stage_name": "sigungu_exact_with_sido",
            "status": "matched",
            "matches": [sigungu_exact[0]],
            "precision": "sigungu",
        }

    # 3단계: 두 어절 조합 매칭 - "시군구 읍면동"(정밀) 또는 "시도(약칭) 시군구(접미사 유연)"(대표좌표)
    if len(tokens) >= 2:
        emd_token = tokens[-1]
        sigungu_part = " ".join(tokens[:-1])

        combo_groups = _group_first_by_sigungu(
            [
                r
                for r in records
                if r["emd_name"] == emd_token
                and (
                    _sigungu_only_part(r["sigungu_name"]) == sigungu_part
                    or _sigungu_stem(_sigungu_only_part(r["sigungu_name"])) == _sigungu_stem(sigungu_part)
                )
            ]
        )

        sido_token, sigungu_token = tokens[0], " ".join(tokens[1:])
        sido_scoped_groups = _group_first_by_sigungu(
            [
                r
                for r in records
                if _sido_stem(_sido_only_part(r["sigungu_name"])) == _sido_stem(sido_token)
                and (
                    _sigungu_only_part(r["sigungu_name"]) == sigungu_token
                    or _sigungu_stem(_sigungu_only_part(r["sigungu_name"])) == _sigungu_stem(sigungu_token)
                )
            ]
        )

        # 시군구+읍면동 조합(정밀 일치)을 시도약칭 조합보다 우선한다 - 더 구체적인 입력이기 때문.
        if len(combo_groups) == 1:
            logger.info("[region_mapper] '%s' -> 3단계(시군구+읍면동 조합, 정밀)", raw_input)
            rep = next(iter(combo_groups.values()))[0]
            return {"stage": 3, "stage_name": "combo_match", "status": "matched", "matches": [rep], "precision": "emd"}
        if len(combo_groups) == 0 and len(sido_scoped_groups) == 1:
            logger.info("[region_mapper] '%s' -> 3단계(시도약칭+시군구 조합, 대표좌표)", raw_input)
            rep = next(iter(sido_scoped_groups.values()))[0]
            return {
                "stage": 3,
                "stage_name": "combo_match",
                "status": "matched",
                "matches": [rep],
                "precision": "sigungu",
            }
        combined = {**combo_groups, **sido_scoped_groups}
        if len(combined) > 1:
            logger.warning("[region_mapper] '%s' -> 3단계에서 %d개 후보로 모호함", raw_input, len(combined))
            return {
                "stage": 3,
                "stage_name": "combo_match",
                "status": "ambiguous",
                "matches": [members[0] for members in combined.values()],
            }

    # 4단계: 시도 없이 시군구명만 정확 일치 (동명이인 여부 확인)
    bare_groups = _group_first_by_sigungu(
        [r for r in records if _sigungu_only_part(r["sigungu_name"]) == compact]
    )
    if bare_groups:
        if len(bare_groups) == 1:
            logger.info("[region_mapper] '%s' -> 4단계(시군구명 단독 정확일치)", raw_input)
            rep = next(iter(bare_groups.values()))[0]
            return {
                "stage": 4,
                "stage_name": "bare_sigungu_exact",
                "status": "matched",
                "matches": [rep],
                "precision": "sigungu",
            }
        logger.warning("[region_mapper] '%s' -> 4단계에서 동명이인 %d건 발견", raw_input, len(bare_groups))
        return {
            "stage": 4,
            "stage_name": "bare_sigungu_exact",
            "status": "ambiguous",
            "matches": [members[0] for members in bare_groups.values()],
        }

    # 5단계: 접미사(시/군/구) 유무를 무시한 유연 매칭
    compact_stem = _sigungu_stem(compact)
    stem_groups = _group_first_by_sigungu(
        [r for r in records if _sigungu_stem(_sigungu_only_part(r["sigungu_name"])) == compact_stem]
    )
    if stem_groups:
        if len(stem_groups) == 1:
            logger.info("[region_mapper] '%s' -> 5단계(접미사 유연 처리)", raw_input)
            rep = next(iter(stem_groups.values()))[0]
            return {
                "stage": 5,
                "stage_name": "suffix_flexible",
                "status": "matched",
                "matches": [rep],
                "precision": "sigungu",
            }
        logger.warning("[region_mapper] '%s' -> 5단계에서 %d개 후보로 모호함", raw_input, len(stem_groups))
        return {
            "stage": 5,
            "stage_name": "suffix_flexible",
            "status": "ambiguous",
            "matches": [members[0] for members in stem_groups.values()],
        }

    # 6단계 "N시 M구" 패턴 처리 - 구가 있는 시(창원시 등)를 "시+구" 형태로 입력한 경우.
    # 토큰이 2개 이상이고 마지막 토큰이 "구"로 끝날 때만 적용되므로, "중구"처럼 시/도
    # 정보 없이 구만 단독으로 들어온 입력(토큰 1개)은 여기 걸리지 않고 기존 4단계
    # (동명이인 처리)로 그대로 넘어간다 - 서로 다른 입력 패턴이라 충돌하지 않는다.
    if len(tokens) >= 2 and tokens[-1].endswith("구"):
        city_part = " ".join(tokens[:-1])
        district_part = " ".join(tokens[-2:])  # "창원시 진해구"

        # 6-a: 구 단위 데이터가 실제로 있으면(sigungu_coordinates.json에 "시도 시군구 구"
        # 레코드가 존재) 그걸로 정밀 확정한다 - 이게 city_part 대표좌표보다 우선이다.
        exact_district = [
            r for r in records
            if " ".join(r["sigungu_name"].split(" ")[1:]) == district_part
        ]
        district_groups = _group_first_by_sigungu(exact_district)
        if len(district_groups) == 1:
            logger.info("[region_mapper] '%s' -> 6단계(시+구 정밀일치, 구 단위 데이터 사용)", raw_input)
            rep = next(iter(district_groups.values()))[0]
            return {
                "stage": 6,
                "stage_name": "city_district_exact",
                "status": "matched",
                "matches": [rep],
                "precision": "sigungu",
                "match_type": None,
            }

        # 6-b: 구 단위 데이터가 없으면(알려진 데이터 갭이 아직 남은 도시) 입력의 "시" 부분이
        # 유일하게 존재하는 시군구일 때(=동명이인 우려 없음) 그 시의 대표좌표로 확정한다.
        exact_city = [r for r in records if r["sigungu_name"] == city_part]
        bare_city = [r for r in records if _sigungu_only_part(r["sigungu_name"]) == city_part]
        city_groups = _group_first_by_sigungu(exact_city + bare_city)
        if len(city_groups) == 1:
            logger.info(
                "[region_mapper] '%s' -> 6단계 fallback(시+구 패턴, '%s' 대표좌표로 확정)",
                raw_input, city_part,
            )
            rep = next(iter(city_groups.values()))[0]
            return {
                "stage": 6,
                "stage_name": "city_fallback",
                "status": "matched",
                "matches": [rep],
                "precision": "sigungu",
                "match_type": "city_fallback",
            }

    # 6단계: 부분 문자열 포함 매칭 - 확정하지 않고 후보만 제시
    # stem이 1글자면("동구"->"동" 등) 아무 입력에나 우연히 포함되기 쉬워 신뢰할 수 없으므로
    # 양방향 포함 관계 검사 모두에서 2글자 미만 stem은 제외한다.
    MIN_PARTIAL_STEM_LEN = 2
    partial_groups = _group_first_by_sigungu(
        [
            r
            for r in records
            if len(_sigungu_stem(_sigungu_only_part(r["sigungu_name"]))) >= MIN_PARTIAL_STEM_LEN
            and (
                _sigungu_stem(_sigungu_only_part(r["sigungu_name"])) in compact
                or compact in _sigungu_stem(_sigungu_only_part(r["sigungu_name"]))
            )
        ]
    )
    if len(partial_groups) > 1:
        # 상위(시) 레코드와 하위(시+구) 레코드가 함께 후보로 잡히는 경우가 있다(예: 입력이
        # "청주시 상당구 가덕면"인데 "충청북도 청주시"와 "충청북도 청주시 상당구"가 둘 다
        # 부분일치로 걸림). 입력에 하위 구/군 이름이 더 구체적으로 들어있으므로, 다른 후보의
        # sigungu_name을 그대로 접두어로 포함하는(=상위인) 후보는 제거하고 더 구체적인
        # 쪽을 우선한다.
        names = list(partial_groups.keys())
        to_drop = {a for a in names for b in names if a != b and b.startswith(a + " ")}
        if to_drop:
            partial_groups = {k: v for k, v in partial_groups.items() if k not in to_drop}

    if len(partial_groups) > 1:
        # 공백을 없앤 compact 문자열로 부분일치를 검사하다 보니, 서로 다른 단어가 이어지는
        # 경계에서 우연히 다른 지명과 겹치는 경우가 있다(예: "청주시"+"흥덕구"가 이어지며
        # "시흥"(시흥시)이 우연히 생기거나, "여주시"+"하동"의 "하동"이 하동군과 우연히
        # 겹치는 등). 반면 진짜 대상 후보는 입력을 공백으로 나눈 실제 단어(tokens)와
        # 정확히 일치하는 경우가 많으므로, 그런 정확 일치 후보가 하나라도 있으면 그 후보(들)만
        # 남기고 나머지(=단어 경계를 무시한 우연한 부분일치)는 후보에서 제외한다.
        exact_token_keys = {k for k in partial_groups if _sigungu_only_part(k) in tokens}
        if exact_token_keys:
            partial_groups = {k: v for k, v in partial_groups.items() if k in exact_token_keys}

    if len(partial_groups) == 1:
        # 3~5단계와 동일한 규칙 - 후보가 하나뿐이면 동명이인 우려가 없으므로 바로 확정한다.
        # 예전에는 부분일치 단계에 도달하면 후보 수와 무관하게 항상 ambiguous로 빠졌는데,
        # 실제로는 읍/면/동이 좌표 데이터에 없을 뿐 시군구 자체는 유일하게 식별되는 경우가
        # 대부분이라(예: "여주시 오학동"처럼 여주시의 특정 동이 데이터에 없는 경우),
        # 이 사용자 확인 없이 시군구 대표좌표로 매칭되지 못해 적합도 계산 자체가 실패했다.
        logger.info("[region_mapper] '%s' -> 6단계(부분일치, 후보 1개 -> 시군구 대표좌표로 확정)", raw_input)
        rep = next(iter(partial_groups.values()))[0]
        return {
            "stage": 6,
            "stage_name": "partial_unique",
            "status": "matched",
            "matches": [rep],
            "precision": "sigungu",
            "match_type": "partial_unique",
        }

    if partial_groups:
        logger.warning("[region_mapper] '%s' -> 6단계(부분일치) %d개 후보, 사용자 확인 필요", raw_input, len(partial_groups))
        return {
            "stage": 6,
            "stage_name": "partial",
            "status": "ambiguous",
            "matches": [members[0] for members in partial_groups.values()],
        }

    # 7단계: 전부 실패
    logger.info("[region_mapper] '%s' -> 7단계(매칭 실패)", raw_input)
    return {"stage": 7, "stage_name": "not_found", "status": "not_found", "matches": []}


def _resolve_region_match(region_name, sigungu_data=None):
    """region_name -> 좌표가 있는 sigungu 레코드 하나로 정규화하는 공통 로직.

    find_nearest_station()과 find_nearest_station_for_crop()이 "지역명을 좌표로
    바꾸는" 부분(모호한 이름 처리 포함)을 공유하기 위해 분리했다 - 관측소 후보를
    무엇으로 좁힐지는 호출부마다 다르지만, 좌표 변환 로직은 완전히 동일하다.

    반환: (error_dict, None) - 실패(빈 입력/not_found/ambiguous). error_dict는
          그대로 반환해도 되는 {"status", "input", ...} 형태.
          (None, resolved) - 성공. resolved = {"matched", "precision", "stage",
          "stage_name", "match_type"}. match_type은 보통 None이고, 6단계
          fallback(시+구 패턴 자동확정)으로 매칭됐을 때만 "city_fallback" -
          프론트엔드 노출용이 아니라 내부 로깅/디버깅용 표시다.
    """
    if not region_name or not region_name.strip():
        return {"status": "not_found", "input": region_name, "message": "지역명이 비어 있습니다."}, None

    records = sigungu_data if sigungu_data is not None else _load_sigungu_records()
    if not records:
        return {
            "status": "not_found",
            "input": region_name,
            "message": f"{SIGUNGU_COORDS_PATH} 좌표 데이터를 불러올 수 없습니다.",
        }, None

    result = _match_region(region_name, records)

    if result["status"] == "not_found":
        return {"status": "not_found", "input": region_name, "message": "지원하지 않는 지역명입니다."}, None

    if result["status"] == "ambiguous":
        return {
            "status": "ambiguous",
            "input": region_name,
            "candidates": [
                {"full_name": r["sigungu_name"], "region_code": _derive_sigungu_code(r["emd_code"])}
                for r in result["matches"]
            ],
        }, None

    matched = result["matches"][0]
    return None, {
        "matched": matched,
        "precision": result["precision"],
        "stage": result["stage"],
        "stage_name": result["stage_name"],
        "match_type": result.get("match_type"),
    }


def find_nearest_station(region_name, sigungu_data=None, station_data=None):
    """행정구역명을 받아 가장 가까운 기상 관측소를 반환한다.

    반환 형태:
    - 매칭 성공: {"status": "matched", "input", "match_stage", "match_stage_name",
                  "matched_region": {...}, "station": {...}, "distance_km"}
    - 동명이인/부분일치로 특정 불가: {"status": "ambiguous", "input", "candidates": [...]}
      (이 경우 관측소 매핑은 하지 않고 candidates만 반환한다)
    - 지원하지 않는 지역명: {"status": "not_found", "input", "message"}
    """
    error, resolved = _resolve_region_match(region_name, sigungu_data)
    if error is not None:
        return error

    matched = resolved["matched"]
    stations = station_data if station_data is not None else _load_stations()
    if not stations:
        return {
            "status": "not_found",
            "input": region_name,
            "message": f"{STATION_MAP_PATH} 관측소 데이터를 불러올 수 없습니다.",
        }

    nearest = min(stations, key=lambda st: haversine_distance(matched["lat"], matched["lon"], st["lat"], st["lon"]))
    distance_km = haversine_distance(matched["lat"], matched["lon"], nearest["lat"], nearest["lon"])

    precision = resolved["precision"]
    return {
        "status": "matched",
        "input": region_name,
        "match_stage": resolved["stage"],
        "match_stage_name": resolved["stage_name"],
        "match_type": resolved.get("match_type"),
        "matched_region": {
            "sigungu_name": matched["sigungu_name"],
            "emd_name": matched["emd_name"] if precision == "emd" else None,
            "precision": precision,
            "lat": matched["lat"],
            "lon": matched["lon"],
        },
        "station": {
            "station_id": nearest["station_id"],
            "station_name": nearest["station_name"],
            "lat": nearest["lat"],
            "lon": nearest["lon"],
            "cluster_id": nearest["cluster_id"],
            "cluster_name": nearest.get("cluster_name"),
        },
        "distance_km": round(distance_km, 2),
    }


def find_nearest_station_for_crop(region_name, crop, sigungu_data=None, station_data=None):
    """region_name을 좌표로 바꾼 뒤, 전체 관측소가 아니라 crop_station_registry의
    CROP_STATION_REGISTRY[crop]에 등록된(=이 작물의 근거값이 실제로 있는) 관측소들
    중에서만 최근접을 찾는다. 좌표 변환은 find_nearest_station()과 완전히 동일한
    로직(_resolve_region_match)을 재사용한다.

    crop이 CROP_STATION_REGISTRY에 없으면 ValueError.

    반환 형태:
    - 매칭 성공: {"status": "matched", "input_region", "crop", "matched_station",
                  "distance_km", "warning": None|str, ...registry 항목의 부가정보
                  (cultivation_type 또는 calendar_quality)}
      distance_km이 DISTANCE_WARNING_THRESHOLD_KM(기본 80km)보다 크면 warning에
      "근거 관측소와 거리가 멀어 정확도가 낮을 수 있습니다"가 채워진다.
    - 동명이인/부분일치: {"status": "ambiguous", "input_region", "crop", "candidates"}
    - 지원하지 않는 지역명: {"status": "not_found", "input_region", "crop", "message"}
    """
    if crop not in CROP_STATION_REGISTRY:
        raise ValueError(
            f"지원하지 않는 작물명입니다: '{crop}' (지원 작물: {', '.join(CROP_STATION_REGISTRY.keys())})"
        )

    error, resolved = _resolve_region_match(region_name, sigungu_data)
    if error is not None:
        return {
            "status": error["status"],
            "input_region": region_name,
            "crop": crop,
            **{k: v for k, v in error.items() if k not in ("status", "input")},
        }

    matched = resolved["matched"]
    all_stations = station_data if station_data is not None else _load_stations()
    stations_by_name = {s["station_name"]: s for s in all_stations}

    candidates = []
    for entry in CROP_STATION_REGISTRY[crop]:
        station_name = entry["station"]
        station_coord = stations_by_name.get(station_name)
        if station_coord is None:
            logger.warning(
                "[region_mapper] crop_station_registry의 '%s'(%s 작물) 좌표를 %s에서 "
                "찾을 수 없어 후보에서 제외합니다.",
                station_name, crop, STATION_MAP_PATH,
            )
            continue
        candidates.append((entry, station_coord))

    if not candidates:
        return {
            "status": "not_found",
            "input_region": region_name,
            "crop": crop,
            "message": f"'{crop}'에 등록된 관측소 중 좌표를 찾을 수 있는 곳이 없습니다.",
        }

    best_entry, best_station = min(
        candidates,
        key=lambda pair: haversine_distance(matched["lat"], matched["lon"], pair[1]["lat"], pair[1]["lon"]),
    )
    distance_km = haversine_distance(matched["lat"], matched["lon"], best_station["lat"], best_station["lon"])

    extra_fields = {k: v for k, v in best_entry.items() if k != "station"}
    warning = (
        "근거 관측소와 거리가 멀어 정확도가 낮을 수 있습니다"
        if distance_km > DISTANCE_WARNING_THRESHOLD_KM
        else None
    )

    return {
        "status": "matched",
        "input_region": region_name,
        "crop": crop,
        "matched_station": best_entry["station"],
        **extra_fields,
        "distance_km": round(distance_km, 2),
        "warning": warning,
        "match_type": resolved["match_type"],
    }
