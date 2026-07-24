"""hourly_temp_fruit_full.csv를 관측소별 시계열로 만들어 temperature_duration_rule의
개화기 냉해(작물별 순간 한계온도)/일소 규칙을 관측소x연도별로 판정한다.

⚠️ 2026-07-23: temperature_duration_rule의 "동상해 0℃/48h 지속" 규칙이 원문 재검증
결과 폐기되고, 작물별(사과/배) 개화단계 순간 한계온도 판정으로 전면 교체됐다.

각 관측소는 실제 재배 작물 하나로 고정되어 있어(STATION_CROP), 그 작물 체크만 돌린다
(예: 영주는 사과만, 배 체크는 아예 호출하지 않음). 이전에는 6개 관측소 전부에 사과·배를
둘 다 돌려서 "그 관측소용 실측 캘린더가 없어 구조적으로 항상 False"인 무의미한 행이
섞여 있었는데, 이 매핑으로 그런 행 자체가 안 생기게 했다.
- 사과: 영주, 안동, 문경, 거창
- 배: 천안, 광주 (배 실측 캘린더가 있는 관측소와 정확히 일치)

연도 경계(예: 개화기가 해 경계를 걸치는 경우는 없지만, 만일을 대비해) 연도별로 잘라서
보는 한계는 이전과 동일하게 남아있다.
"""

import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent  # farm-guide/data/
BASE_DIR = DATA_DIR.parent  # farm-guide/
HOURLY_CSV_PATH = DATA_DIR / "raw" / "hourly_temp_fruit_full.csv"
OUTPUT_CSV_PATH = DATA_DIR / "processed" / "apple_pear_duration_events.csv"

sys.path.insert(0, str(BASE_DIR / "backend" / "scoring"))
from temperature_duration_rule import (  # noqa: E402
    HEAT_DURATION_DAYS,
    HEAT_THRESHOLD,
    check_apple_pear_temperature_risk,
)

STATION_CROP = {
    "영주": "사과",
    "안동": "사과",
    "문경": "사과",
    "거창": "사과",
    "천안": "배",
    "광주": "배",
}


def load_hourly_by_station(csv_path=HOURLY_CSV_PATH):
    """station -> [(datetime, temp), ...] (시간 오름차순, 결측 temp는 제외)."""
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["temp"]).sort_values(["station", "date"])

    records_by_station = {}
    for station, group in df.groupby("station"):
        records_by_station[station] = list(zip(group["date"], group["temp"]))
    return records_by_station


def _worst_frost_fields(frost_detail):
    worst = frost_detail["worst_event"]
    if not worst:
        return {
            "frost_worst_datetime": None,
            "frost_worst_stage": None,
            "frost_worst_temp": None,
            "frost_worst_threshold": None,
            "frost_worst_margin": None,
        }
    return {
        "frost_worst_datetime": worst["datetime"],
        "frost_worst_stage": worst["stage"],
        "frost_worst_temp": worst["temp"],
        "frost_worst_threshold": worst["threshold"],
        "frost_worst_margin": round(worst["margin"], 1),
    }


def _near_miss_fields(frost_detail):
    near_miss = frost_detail.get("worst_near_miss")
    if not near_miss:
        return {
            "near_miss_datetime": None,
            "near_miss_stage": None,
            "near_miss_margin": None,
        }
    return {
        "near_miss_datetime": near_miss["datetime"],
        "near_miss_stage": near_miss["stage"],
        "near_miss_margin": round(near_miss["margin"], 1),
    }


def _max_heat_days(heat_detail):
    days = [p["days"] for p in heat_detail["all_hot_periods"]]
    return max(days) if days else None


def check_all_stations_by_year(records_by_station):
    rows = []
    for station, records in sorted(records_by_station.items()):
        if station not in STATION_CROP:
            raise ValueError(f"'{station}' 관측소의 재배 작물이 STATION_CROP에 없습니다")
        crop = STATION_CROP[station]

        years = sorted({dt.year for dt, _ in records})
        for year in years:
            year_records = [(dt, temp) for dt, temp in records if dt.year == year]
            result = check_apple_pear_temperature_risk(year_records, crop, station_name=station)
            frost = result["frost_detail"]
            heat = result["heat_detail"]

            rows.append(
                {
                    "station": station,
                    "year": year,
                    "crop": crop,
                    "data_start": year_records[0][0],
                    "data_end": year_records[-1][0],
                    "n_hourly_records": len(year_records),
                    "risk": result["risk"],
                    "reason": result["reason"],
                    "frost_triggered": frost["triggered"],
                    "frost_event_count": len(frost["events"]),
                    **_worst_frost_fields(frost),
                    **_near_miss_fields(frost),
                    "heat_triggered": heat["triggered"],
                    "heat_threshold_c": HEAT_THRESHOLD,
                    "heat_required_days": HEAT_DURATION_DAYS,
                    "heat_qualifying_periods": len(heat["triggered_periods"]),
                    "heat_max_consecutive_days": _max_heat_days(heat),
                }
            )
    return pd.DataFrame(rows)


def print_yearly_summary(events_df):
    print("\n=== 연도x작물별 트리거 요약 (관측소 6곳 기준) ===")
    summary = (
        events_df.groupby(["year", "crop"])
        .agg(
            트리거_관측소수=("risk", lambda s: (s == "높음").sum()),
            개화기냉해_트리거=("frost_triggered", "sum"),
            일소_트리거=("heat_triggered", "sum"),
            판정_관측소수=("station", "nunique"),
        )
        .reset_index()
    )
    print(summary.to_string(index=False))

    triggered = events_df[events_df["risk"] == "높음"]
    if not triggered.empty:
        print("\n=== 트리거된 관측소x연도x작물 상세 ===")
        detail_cols = [
            "station", "year", "crop", "frost_triggered", "frost_worst_stage",
            "frost_worst_temp", "frost_worst_threshold", "heat_triggered",
            "heat_max_consecutive_days", "reason",
        ]
        print(triggered[detail_cols].to_string(index=False))


def main():
    records_by_station = load_hourly_by_station()
    events_df = check_all_stations_by_year(records_by_station)
    events_df.to_csv(OUTPUT_CSV_PATH, index=False, encoding="utf-8-sig")
    print(
        f"저장 완료: {OUTPUT_CSV_PATH} ({len(events_df)}행, 관측소 {events_df['station'].nunique()}곳 "
        f"x 연도 {events_df['year'].nunique()}개 x 작물 {events_df['crop'].nunique()}종)"
    )
    print_yearly_summary(events_df)


if __name__ == "__main__":
    main()
