"""
작물별로 실제 근거값(near/위험값, 개화캘린더 등)이 확보된 관측소 레지스트리.

기존 region_mapper.py의 find_nearest_station()은 "전국 88개 관측소 중 최근접"을
찾지만, 여기서는 그 88개 중에서도 "이 작물의 근거값이 실제로 있는 관측소"로
후보를 좁혀야 한다. 예를 들어 논산시가 입력되면 지리적 최근접은 대전일 수 있지만,
오이 근거값이 있는 관측소(순천·진주·밀양·부여·대전) 중 최근접을 찾아야 스코어링에
쓸 수 있는 결과가 나온다.

각 항목의 station 이름은 reference_data.py의 근거값 dict 키와 정확히 일치해야 한다.
"""

CROP_STATION_REGISTRY = {
    "사과": [
        {"station": "영주", "calendar_quality": "실측(만개일 2021~2026 평균)"},
        {"station": "안동", "calendar_quality": "근사(_generic)"},
        {"station": "문경", "calendar_quality": "근사(_generic)"},
        {"station": "거창", "calendar_quality": "실측(만개일 2021~2026 평균)"},
    ],
    "배": [
        {"station": "천안", "calendar_quality": "실측(발아~만개 2020~2026 평균)"},
        {"station": "광주", "calendar_quality": "실측(나주 대체, 발아~만개 2020~2026 평균)"},
    ],
    "오이": [
        {"station": "순천", "cultivation_type": "촉성재배"},
        {"station": "진주", "cultivation_type": "촉성재배"},
        {"station": "밀양", "cultivation_type": "촉성재배"},
        {"station": "부여", "cultivation_type": "반촉성재배"},
        {"station": "대전", "cultivation_type": "반촉성재배"},
    ],
    "감자": [
        {"station": "서산", "cultivation_type": "봄재배"},
        {"station": "밀양", "cultivation_type": "봄재배"},
        {"station": "대관령", "cultivation_type": "고랭지재배"},
    ],
    "상추": [
        {"station": "대관령", "cultivation_type": "고랭지재배"},
        {"station": "태백", "cultivation_type": "고랭지재배"},
        {"station": "정선군", "cultivation_type": "고랭지재배"},
        {"station": "남해", "cultivation_type": "저지대재배"},
    ],
}

# 이 거리(km)보다 멀면 "근거 관측소와 너무 떨어져 있다"는 경고를 반환해야 함.
# (예: 제주도에서 감자를 조회하면 대관령까지의 거리가 아주 멀어질 것)
DISTANCE_WARNING_THRESHOLD_KM = 80
