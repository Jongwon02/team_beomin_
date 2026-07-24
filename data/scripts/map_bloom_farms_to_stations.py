"""pear_bloom_dates.csv의 관측농장(나주/완주/영천/사천/이천/김제/천안)을 우리 6개
기상관측소(영주/안동/문경/거창/천안/광주)에 지리적으로 가장 가까운 농장으로 매칭하고,
그 농장의 실측 발아(ecln)/만개(flblms) 시기가 현재 DEFAULT_SPRING_FROST_WINDOW(3/1~6/10)의
시작일(3/1)보다 항상 늦게 시작하는지 검증한다.

⚠️ 6/10(종료일)은 만개 시점이 아니라 "적과종료"(약관상 동상해 적용 종료 시점, 만개 후
4~8주 뒷쪽 시기) 근사치라서, 이 스크립트의 bloom 데이터(발아~만개)만으로는 6/10이
맞는지 검증할 수 없다 - 적과종료 실측 자료가 별도로 있어야 한다. 여기서는 시작일(3/1)
검증과 지역별 발아/만개 시기 차이 파악까지만 한다.
"""

import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent  # farm-guide/data/
BASE_DIR = DATA_DIR.parent  # farm-guide/
BLOOM_CSV_PATH = DATA_DIR / "raw" / "pear_bloom_dates.csv"
STATION_MAP_PATH = DATA_DIR / "processed" / "region_cluster_map.json"
OUTPUT_CSV_PATH = DATA_DIR / "processed" / "station_bloom_window_check.csv"

sys.path.insert(0, str(BASE_DIR / "backend" / "utils"))
from region_mapper import find_nearest_station, haversine_distance  # noqa: E402

OUR_STATIONS = ["영주", "안동", "문경", "거창", "천안", "광주"]

import json  # noqa: E402


def load_station_coords():
    stations = json.load(open(STATION_MAP_PATH, encoding="utf-8"))
    coords = {}
    for name in OUR_STATIONS:
        matches = [s for s in stations if s["station_name"] == name]
        if not matches:
            raise ValueError(f"{name} 관측소 좌표를 region_cluster_map.json에서 못 찾음")
        coords[name] = (matches[0]["lat"], matches[0]["lon"])
    return coords


def load_farm_coords(farm_names):
    coords = {}
    for farm in farm_names:
        result = find_nearest_station(farm)
        if result["status"] != "matched":
            print(f"  ⚠️ '{farm}' 좌표 매칭 실패({result['status']}) - 건너뜀")
            continue
        region = result["matched_region"]
        coords[farm] = (region["lat"], region["lon"])
    return coords


def nearest_farm(station_latlon, farm_coords):
    best_farm, best_dist = None, float("inf")
    for farm, (flat, flon) in farm_coords.items():
        dist = haversine_distance(station_latlon[0], station_latlon[1], flat, flon)
        if dist < best_dist:
            best_farm, best_dist = farm, dist
    return best_farm, best_dist


def main():
    bloom_df = pd.read_csv(BLOOM_CSV_PATH, encoding="utf-8-sig")
    bloom_df["ecln_datetm"] = pd.to_datetime(bloom_df["ecln_datetm"])
    bloom_df["flblms_datetm"] = pd.to_datetime(bloom_df["flblms_datetm"])

    station_coords = load_station_coords()
    farm_coords = load_farm_coords(sorted(bloom_df["farm_name"].unique()))

    rows = []
    for station in OUR_STATIONS:
        if station in farm_coords:
            # 관측소명과 정확히 같은 이름의 농장이 있으면(천안) 그걸 그대로 쓴다.
            farm, dist = station, 0.0
        else:
            farm, dist = nearest_farm(station_coords[station], farm_coords)

        farm_rows = bloom_df[bloom_df["farm_name"] == farm]
        earliest_ecln = farm_rows["ecln_datetm"].min()
        latest_flblms = farm_rows["flblms_datetm"].max()
        latest_ecln = farm_rows["ecln_datetm"].max()
        earliest_flblms = farm_rows["flblms_datetm"].min()

        rows.append(
            {
                "station": station,
                "nearest_bloom_farm": farm,
                "distance_km": round(dist, 1),
                "n_years_observed": farm_rows["year"].nunique(),
                "earliest_ecln_monthday": earliest_ecln.strftime("%m-%d"),
                "latest_ecln_monthday": latest_ecln.strftime("%m-%d"),
                "earliest_flblms_monthday": earliest_flblms.strftime("%m-%d"),
                "latest_flblms_monthday": latest_flblms.strftime("%m-%d"),
                "starts_after_window_start(3/1)": earliest_ecln.month > 3
                or (earliest_ecln.month == 3 and earliest_ecln.day >= 1),
            }
        )

    result_df = pd.DataFrame(rows)
    result_df.to_csv(OUTPUT_CSV_PATH, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUTPUT_CSV_PATH}\n")
    print(result_df.to_string(index=False))

    print(
        "\n⚠️ 종료일(6/10)은 만개일이 아니라 '적과종료' 근사치라 이 데이터(발아~만개)만으로는"
        " 검증 불가 - 위 표는 시작일(3/1)이 실제 발아 시작보다 항상 앞서는지, 그리고"
        " 지역별 발아/만개 시기가 얼마나 차이나는지까지만 보여준다."
    )


if __name__ == "__main__":
    main()
