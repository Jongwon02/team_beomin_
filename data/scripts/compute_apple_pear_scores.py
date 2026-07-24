"""hourly_temp_fruit_full.csv의 관측소별 시간단위 기온 시계열을 scoring_engine.score_crop()에
hourly_temp_records/station_name으로 실제로 넘겨서, 2020~2026 사과·배 온도 점수를 계산한다.

관측소↔작물 매핑은 apple_pear_duration_check.py와 동일하다:
- 사과: 영주, 안동, 문경, 거창
- 배: 천안, 광주

⚠️ 이 스크립트는 "온도만" 계산한다. 강수/일조/pH/유기물/유효인산/EC는 이 6개 기상관측소에
대해 실측값(usable_readings)이 없어서(토양 데이터가 없음), score_crop()에 빈 dict를
넘긴다. score_crop()은 값이 없는 변수는 breakdown에서 그냥 건너뛰고 남은 가중치(온도)를
100%로 재정규화하므로, 결과적으로 total_score는 사실상 "온도 단독 점수"와 같아진다.
즉 이 스크립트의 total_score를 "종합 적합도 점수"로 오해하면 안 되고, 온도-정밀판정
(hourly_temp_records 경로)이 score_crop에 실제로 연결되는지 확인/활용하는 용도다.
"""

import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent  # farm-guide/data/
BASE_DIR = DATA_DIR.parent  # farm-guide/
HOURLY_CSV_PATH = DATA_DIR / "raw" / "hourly_temp_fruit_full.csv"
OUTPUT_CSV_PATH = DATA_DIR / "processed" / "apple_pear_scores.csv"

sys.path.insert(0, str(BASE_DIR / "backend" / "scoring"))
from scoring_engine import score_crop  # noqa: E402
from temperature_duration_rule import score_apple_pear_temperature  # noqa: E402

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


def _near_miss_fields(frost_detail):
    near_miss = frost_detail.get("worst_near_miss")
    if not near_miss:
        return {"near_miss_datetime": None, "near_miss_stage": None, "near_miss_margin": None}
    return {
        "near_miss_datetime": near_miss["datetime"],
        "near_miss_stage": near_miss["stage"],
        "near_miss_margin": round(near_miss["margin"], 1),
    }


def _heat_near_miss_fields(heat_detail):
    """check_heat_margin의 worst_near_miss(연속 2일 중 더 낮은 날 최고기온 기준 margin)."""
    near_miss = heat_detail.get("worst_near_miss")
    if not near_miss:
        return {
            "heat_margin": None, "heat_day1": None, "heat_day2": None,
            "heat_day1_max": None, "heat_day2_max": None,
        }
    return {
        "heat_margin": round(near_miss["margin"], 1),
        "heat_day1": near_miss["day1"],
        "heat_day2": near_miss["day2"],
        "heat_day1_max": near_miss["day1_max"],
        "heat_day2_max": near_miss["day2_max"],
    }


def compute_all_scores(records_by_station):
    rows = []
    for station, records in sorted(records_by_station.items()):
        if station not in STATION_CROP:
            raise ValueError(f"'{station}' 관측소의 재배 작물이 STATION_CROP에 없습니다")
        crop = STATION_CROP[station]

        years = sorted({dt.year for dt, _ in records})
        for year in years:
            year_records = [(dt, temp) for dt, temp in records if dt.year == year]

            # 실제로 score_crop()에 hourly_temp_records/station_name을 넘긴다.
            # usable_readings는 빈 dict — 이 관측소들엔 강수/일조/pH/유기물/유효인산/EC
            # 실측값이 없어서(위 모듈 docstring 참고), 온도 외 변수는 자동으로 건너뛴다.
            result = score_crop(
                crop,
                usable_readings={},
                hourly_temp_records=year_records,
                station_name=station,
            )

            # frost_score/heat_score 세부값은 score_crop 반환값에 없어서, 같은 판정을
            # 별도로 한 번 더 호출해 근거(near-miss margin 등)까지 같이 남긴다.
            temp_detail = score_apple_pear_temperature(year_records, crop, station_name=station)
            frost = temp_detail["detail"]["frost"]
            heat = temp_detail["detail"]["heat"]

            rows.append(
                {
                    "station": station,
                    "year": year,
                    "crop": crop,
                    "n_hourly_records": len(year_records),
                    "total_score": result["total_score"],
                    "temperature_source": result["temperature_source"],
                    "excluded_no_reference": ",".join(result["excluded_no_reference"]),
                    "frost_score": temp_detail["frost_score"],
                    "heat_score": temp_detail["heat_score"],
                    "frost_triggered": frost["triggered"],
                    "heat_real_2day_heatwave": bool(
                        heat.get("worst_near_miss") and heat["worst_near_miss"]["margin"] > 0
                    ),
                    **_near_miss_fields(frost),
                    **_heat_near_miss_fields(heat),
                }
            )
    return pd.DataFrame(rows)


def main():
    records_by_station = load_hourly_by_station()
    scores_df = compute_all_scores(records_by_station)
    scores_df.to_csv(OUTPUT_CSV_PATH, index=False, encoding="utf-8-sig")
    print(
        f"저장 완료: {OUTPUT_CSV_PATH} ({len(scores_df)}행, 관측소 {scores_df['station'].nunique()}곳 "
        f"x 연도 {scores_df['year'].nunique()}개)"
    )
    print(scores_df.to_string(index=False))

    print("\n=== 작물별 평균 온도 점수 ===")
    print(scores_df.groupby("crop")["total_score"].agg(["mean", "min", "max"]).round(1))


if __name__ == "__main__":
    main()
