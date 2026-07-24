"""weather.get_short_term_forecast 실제 API 호출 테스트 (서울)."""

import json
import logging
import sys
from pathlib import Path

from weather import get_forecast_for_matched_region, get_latest_base_datetime, get_short_term_forecast, latlon_to_grid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from region_mapper import find_nearest_station  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")

SEOUL_LAT, SEOUL_LON = 37.5665, 126.9780  # 서울시청 실제 좌표


def main():
    nx, ny = latlon_to_grid(SEOUL_LAT, SEOUL_LON)
    base_date, base_time = get_latest_base_datetime()
    print(f"서울 격자좌표: nx={nx}, ny={ny} (실제 기상청 값: nx=60, ny=127)")
    print(f"계산된 base_date/base_time: {base_date} {base_time}")

    print("\n=== 1) get_short_term_forecast(lat, lon) 직접 호출 ===")
    result = get_short_term_forecast(SEOUL_LAT, SEOUL_LON)
    if result is None:
        print("호출 실패 (None 반환) - 로그 확인 필요")
    else:
        print(f"nx={result['nx']}, ny={result['ny']}, base_date={result['base_date']}, base_time={result['base_time']}")
        dates = sorted(result["forecast"].keys())
        print(f"예보 날짜 수: {len(dates)} ({dates})")
        first_date = dates[0]
        first_time = sorted(result["forecast"][first_date].keys())[0]
        print(f"샘플 ({first_date} {first_time}): {json.dumps(result['forecast'][first_date][first_time], ensure_ascii=False)}")

    print("\n=== 2) region_mapper.find_nearest_station() 연계 호출 ===")
    region_match = find_nearest_station("서울특별시 종로구")
    print(f"region_mapper 결과 status={region_match.get('status')}, matched_region={region_match.get('matched_region')}")
    forecast_via_region = get_forecast_for_matched_region(region_match)
    if forecast_via_region is None:
        print("연계 호출 실패 (None 반환)")
    else:
        print(f"연계 호출 성공: nx={forecast_via_region['nx']}, ny={forecast_via_region['ny']}")


if __name__ == "__main__":
    main()
