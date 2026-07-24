"""live_scoring.get_live_score() 실제 API 호출 동작 확인용 테스트.

⚠️ 이 스크립트는 실제 외부 API(기상청 단기예보/ASOS 일자료, 흙토람 SoilExamStat)를
   호출합니다. farm-guide/.env에 KMA_SERVICE_KEY / ASOS_DALY_SERVICE_KEY /
   SOIL_EXAM_STAT_SERVICE_KEY가 설정되어 있어야 합니다.
"""

import json
import logging

from live_scoring import get_live_score

logging.basicConfig(level=logging.INFO, format="%(message)s")

TEST_CASES = [
    ("평창군", "감자"),  # -> 대관령(고랭지재배)
    ("천안시", "배"),    # -> 천안(사과·배 정밀 온도 판정 경로)
    ("논산시", "오이"),  # -> 대전(반촉성재배)
]


def main():
    for region, crop in TEST_CASES:
        print(f"\n{'=' * 60}\n{region} + {crop}\n{'=' * 60}")
        result = get_live_score(region, crop)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
