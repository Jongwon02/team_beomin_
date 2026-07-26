"""region_mapper.find_nearest_station / find_nearest_station_for_crop 동작 확인용 테스트."""

import logging

from region_mapper import find_nearest_station, find_nearest_station_for_crop

logging.basicConfig(level=logging.INFO, format="%(message)s")

TEST_CASES = [
    "강원특별자치도 원주시",  # 신 명칭 -> 구 명칭("강원도 원주시") 동의어 매칭, 대표 좌표
    "종로구 계동",  # 시군구+읍면동 정밀 매칭
    "종로구",  # 시군구만 -> 대표 좌표
    "중구",  # 동명이인 6곳 -> ambiguous
    "대구광역시 중구",  # 시도 포함 -> 고유 매칭
    "평창군",  # 정확 일치
    "평창",  # 접미사 유연 처리
    "강원 평창",  # 시도 약칭 + 시군구 조합
    "평창면",  # 부분 일치, 후보 1개 -> 평창군으로 자동 확정 (예전엔 항상 ambiguous였음)
    "없는동네",  # 회귀 테스트: "동구" 1글자 stem 우연 포함 -> not_found여야 함
    "속초시 xyz동네",  # 회귀 테스트: 동구 계열 오염 없이 속초시만 후보로 잡히거나 정상 매칭돼야 함
    "동구",  # 회귀 테스트: 4단계 동명이인 ambiguous, 6단계 수정에 영향받지 않아야 함
    "남구",  # 회귀 테스트: 4단계 동명이인 ambiguous, 6단계 수정에 영향받지 않아야 함
]


def main():
    for name in TEST_CASES:
        result = find_nearest_station(name)
        print(f"\n=== 입력: '{name}' ===")
        print(result)


# (region_name, crop, 기대 확인사항) - 기대값은 근사 확인용 주석이고 실제 판정은 출력을 보고 육안 확인한다.
CROP_TEST_CASES = [
    ("논산시", "오이"),  # -> 부여 또는 대전 근처로 매핑돼야 함
    ("평창군", "감자"),  # -> 대관령(고랭지재배)으로 매핑돼야 함
    ("평창군", "상추"),  # -> 정선군(고랭지재배)으로 매핑돼야 함 (대관령·태백보다 근접)
    ("천안시", "배"),  # -> 천안(실측)으로 매핑, distance_km이 작아야 함
    ("제주시", "감자"),  # -> 아주 먼 지역 -> warning이 떠야 함
]


def test_jeju_old_sido_name_with_dong_matches():
    """회귀 테스트: 프론트엔드가 보내는 구 명칭 "제주도" + 시군구 + 읍면동 조합이
    ambiguous로 빠지지 않고 matched가 되어야 한다 (제주 전역 추천 미표시 버그)."""
    for region in ("제주도 제주시 건입동", "제주도 서귀포시 강정동", "제주도 제주시 구좌읍"):
        result = find_nearest_station_for_crop(region, "사과")
        assert result["status"] == "matched", f"'{region}' -> {result['status']} (matched여야 함)"


def test_partial_match_single_candidate_auto_resolves():
    """회귀 테스트: 읍/면/동이 좌표 데이터에 없어 6단계(부분일치)까지 내려가더라도,
    후보가 시군구 하나뿐이면 3~5단계처럼 바로 matched로 확정돼야 한다(예전엔 후보 수와
    무관하게 항상 ambiguous였음 - 실제 서비스에서 적합도 계산이 광범위하게 실패한 원인)."""
    for region in (
        "경기도 여주시 오학동", "경기도 화성시 금곡동", "경상북도 김천시 율곡동",
        "평창면", "속초시 xyz동네",
    ):
        result = find_nearest_station(region)
        assert result["status"] == "matched", f"'{region}' -> {result['status']} (matched여야 함)"


def test_partial_match_prefers_more_specific_district():
    """회귀 테스트: 입력에 "시+구"가 구체적으로 들어있으면(예: "청주시 상당구"), 부분일치
    단계에서 상위(시) 레코드가 함께 잡히더라도 더 구체적인 "시+구" 쪽으로 확정돼야 한다."""
    result = find_nearest_station("충청북도 청주시 상당구 가덕면")
    assert result["status"] == "matched"
    assert result["matched_region"]["sigungu_name"] == "충청북도 청주시 상당구"


def test_partial_match_prefers_exact_token_over_boundary_collision():
    """회귀 테스트: compact(공백 제거) 문자열로 부분일치를 검사하다 보니 단어 경계에서
    우연히 다른 지명과 겹치는 경우가 있었다(예: "청주시"+"흥덕구"가 이어지며 "시흥"(시흥시)이
    우연히 생기거나, "남양주시"에 "양주시"가, "여주시 하동"의 "하동"이 하동군과 우연히
    겹치는 등) - 실제 서비스에서 이런 지역들의 적합도 계산이 실패한 원인. 입력을 공백으로
    나눈 실제 단어와 정확히 일치하는 후보를 우선해서 이런 우연한 겹침을 배제해야 한다."""
    cases = {
        "경기도 남양주시 다산동": "경기도 남양주시",
        "경기도 남양주시 퇴계원읍": "경기도 남양주시",
        "경기도 여주시 하동": "경기도 여주시",
        "경기도 화성시 영천동": "경기도 화성시",
        "경기도 화성시 오산동": "경기도 화성시",
        "강원도 영월군 산솔면": "강원도 영월군",
        "충청북도 청주시 상당구 미원면": "충청북도 청주시 상당구",
        "충청북도 청주시 청원구 내수읍": "충청북도 청주시 청원구",
        "충청북도 청주시 흥덕구 강내면": "충청북도 청주시 흥덕구",
    }
    for region, expected_sigungu in cases.items():
        result = find_nearest_station(region)
        assert result["status"] == "matched", f"'{region}' -> {result['status']} (matched여야 함)"
        actual = result["matched_region"]["sigungu_name"]
        assert actual == expected_sigungu, f"'{region}' -> '{actual}' (기대값: '{expected_sigungu}')"


def test_crop_station_mapping():
    for region, crop in CROP_TEST_CASES:
        result = find_nearest_station_for_crop(region, crop)
        print(f"\n=== 입력: '{region}' + 작물 '{crop}' ===")
        print(result)

    print("\n=== 존재하지 않는 작물명('딸기') -> ValueError 확인 ===")
    try:
        find_nearest_station_for_crop("논산시", "딸기")
        print("!! 예외가 발생하지 않았습니다 (버그) !!")
    except ValueError as e:
        print(f"ValueError 정상 발생: {e}")


if __name__ == "__main__":
    main()
    print("\n\n" + "=" * 60)
    print("find_nearest_station_for_crop 테스트")
    print("=" * 60)
    test_crop_station_mapping()
