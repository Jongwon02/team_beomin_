"""사용자가 입력한 행정구역명(시군구/읍면동)을 가장 가까운 기상 관측소에 매핑한다."""

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
    return s


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


def _load_sigungu_records(path=SIGUNGU_COORDS_PATH):
    raw = _load_json(path)
    records = []
    for r in raw:
        records.append(
            {
                "emd_code": r["emd_code"],
                "full_name": _canonicalize_sido_prefix(_normalize_whitespace(r["full_name"])),
                "sigungu_name": _canonicalize_sido_prefix(_normalize_whitespace(r["sigungu_name"])),
                "emd_name": _normalize_whitespace(r["emd_name"]),
                "lat": r["lat"],
                "lon": r["lon"],
            }
        )
    return records


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
    normalized = _canonicalize_sido_prefix(_normalize_whitespace(raw_input))
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
          "stage_name"}.
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
    }
