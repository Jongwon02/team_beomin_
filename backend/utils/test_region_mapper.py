"""region_mapper.find_nearest_station 동작 확인용 테스트."""

import logging

from region_mapper import find_nearest_station

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
    "평창면",  # 부분 일치 -> candidates (정상 유지 확인)
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


if __name__ == "__main__":
    main()
