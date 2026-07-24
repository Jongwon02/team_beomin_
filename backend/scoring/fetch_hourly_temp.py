"""
기상청 API허브(apihub.kma.go.kr) 종관기상관측(ASOS) 시간자료 기간조회로
사과·배 관측소의 시간별 기온을 받아온다.

⚠️ 이 스크립트는 외부 네트워크(apihub.kma.go.kr) 접속이 필요합니다.
   Claude Code나 로컬 터미널에서 실행하세요 (이 대화창의 샌드박스에서는 실행 불가).

사용법:
    python fetch_hourly_temp.py --auth-key YOUR_AUTH_KEY \
        --start 202501010000 --end 202502280000 \
        --out data/raw/hourly_temp_fruit.csv
"""

import argparse
import time
from datetime import datetime, timedelta

import requests
import pandas as pd

BASE_URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm3.php"

# 사과·배 관측소 (이전에 받은 일자료 파일에서 확인한 지점번호)
STATIONS = {
    "영주": 272,
    "안동": 136,
    "문경": 273,
    "거창": 284,
    "천안": 232,
    "광주": 156,
}

MAX_HOURS_PER_CALL = 720  # API 제한: tm2 기준 최대 720시간(30일) 전까지만 조회 가능

# 응답 텍스트의 컬럼 순서 (기상청 API허브 kma_sfctm3.php 응답 형식)
COLUMNS = [
    "TM", "STN", "WD", "WS", "GST_WD", "GST_WS", "GST_TM", "PA", "PS", "PT", "PR",
    "TEMP", "TD", "HM", "PV", "RN", "RN_DAY", "RN_JUN", "RN_INT", "SD_HR3", "SD_DAY",
    "SD_TOT", "WC", "WP", "WW", "CA_TOT", "CA_MID", "CH_MIN", "CT", "CT_TOP", "CT_MID",
    "CT_LOW", "VS", "SS", "SI", "ST_GD", "TS", "TE_005", "TE_01", "TE_02", "TE_03",
    "ST_SEA", "WH", "BF", "IR", "IX",
]


def _fetch_chunk(tm1, tm2, stn, auth_key):
    """tm1~tm2 구간(최대 720시간)의 한 관측소 시간자료를 받아온다."""
    params = {
        "tm1": tm1.strftime("%Y%m%d%H%M"),
        "tm2": tm2.strftime("%Y%m%d%H%M"),
        "stn": stn,
        "help": 0,
        "authKey": auth_key,
    }
    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()

    lines = resp.text.split("\n")
    # 응답 앞뒤에 헤더/푸터 라인이 있어 잘라내야 함(#START7777 등). 실제 데이터만 남긴다.
    data_lines = [
        line for line in lines
        if line.strip() and not line.startswith("#") and not line.startswith("7777")
    ]
    if not data_lines:
        return pd.DataFrame(columns=COLUMNS)

    rows = [line.split() for line in data_lines]
    # 컬럼 수가 안 맞는 줄(파싱 오류 가능성)은 건너뛴다
    rows = [r for r in rows if len(r) == len(COLUMNS)]
    df = pd.DataFrame(rows, columns=COLUMNS)
    return df


def fetch_station_range(station_name, stn_id, start, end, auth_key, sleep_sec=0.5):
    """start~end 전체 기간을 720시간 단위로 쪼개서 반복 호출.

    tm1~tm2는 API 응답에서 양끝을 포함(inclusive)하므로, 청크 경계 시각이
    중복 조회되지 않도록 다음 청크는 이전 청크의 끝 + 1시간부터 시작한다.
    """
    all_chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(hours=MAX_HOURS_PER_CALL - 1), end)
        print(f"  [{station_name}] {cursor} ~ {chunk_end} 요청 중...")
        df = _fetch_chunk(cursor, chunk_end, stn_id, auth_key)
        if not df.empty:
            all_chunks.append(df)
        cursor = chunk_end + timedelta(hours=1)
        time.sleep(sleep_sec)  # API 과호출 방지

    if not all_chunks:
        return pd.DataFrame(columns=["date", "station", "temp"])

    combined = pd.concat(all_chunks, ignore_index=True)
    combined["date"] = pd.to_datetime(combined["TM"], format="%Y%m%d%H%M")
    combined["station"] = station_name
    combined["temp"] = pd.to_numeric(combined["TEMP"], errors="coerce")
    # 기상청 결측값 표기(-9, -99 등)는 NaN으로 변환
    combined.loc[combined["temp"] <= -50, "temp"] = pd.NA
    result = combined[["date", "station", "temp"]].sort_values("date").reset_index(drop=True)
    # 방어적 중복 제거: 청크 경계 수정으로 이제 발생하면 안 되지만, API 쪽 재전송 등
    # 다른 원인의 중복도 대비해 한 번 더 걸러둔다.
    before = len(result)
    result = result.drop_duplicates(subset=["date", "station"], keep="first").reset_index(drop=True)
    if len(result) < before:
        print(f"  [{station_name}] 중복 {before - len(result)}건 제거됨")
    return result


def main():
    parser = argparse.ArgumentParser(description="사과·배 관측소 시간별 기온 수집")
    parser.add_argument("--auth-key", required=True, help="기상청 API허브 인증키")
    parser.add_argument("--start", required=True, help="YYYYMMDDHHMM (KST)")
    parser.add_argument("--end", required=True, help="YYYYMMDDHHMM (KST)")
    parser.add_argument("--out", default="hourly_temp_fruit.csv")
    parser.add_argument("--stations", nargs="*", default=list(STATIONS.keys()),
                         help="받을 관측소 목록(기본값: 6개 전부)")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y%m%d%H%M")
    end = datetime.strptime(args.end, "%Y%m%d%H%M")

    all_stations_df = []
    for name in args.stations:
        stn_id = STATIONS[name]
        print(f"=== {name}({stn_id}) 수집 시작 ===")
        df = fetch_station_range(name, stn_id, start, end, args.auth_key)
        all_stations_df.append(df)
        print(f"  {len(df)}건 수집 완료")

    result = pd.concat(all_stations_df, ignore_index=True)
    result.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"\n완료: {args.out} ({len(result)}행)")
    print(f"결측(temp가 NaN) 개수: {result['temp'].isna().sum()}")


if __name__ == "__main__":
    main()
