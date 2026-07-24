"""
국립원예특작과학원(NIHHS) 과수생육품질관리시스템 - 배 발아/만개기 실측 데이터 수집.

⚠️ 이 스크립트는 외부 네트워크(apis.data.go.kr) 접속이 필요합니다.
   Claude Code나 로컬 터미널에서 실행하세요 (이 대화창의 샌드박스에서는 실행 불가).

data.go.kr "농촌진흥청 국립원예특작과학원_배 생육품질정보 조회 서비스"에서 받은
서비스키를 --service-key로 넘기세요.

✅ 출력결과(Response Element) 필드명은 2026-07-23 실제 --raw 응답으로 확인 완료:
   농장코드=farm_code, 농장명=farm_name, 발아일자=ecln_datetm, 만개일자=flblms_datetm.
   레코드는 <item>이 아니라 <Model> 태그로 온다. Body 레벨에 그 해/전년/평년 평균
   통계(avgEclnDatetm 등)도 같이 내려오며, 이 스크립트는 그것도 각 행에 붙여 저장한다.
   --raw 옵션은 이후 새로운 필드가 추가되는지 재확인할 때 계속 쓸 수 있다.

사용법:
    # 1단계: 원본 XML 확인 (반드시 먼저 실행)
    python fetch_pear_bloom_dates.py --service-key YOUR_KEY --year 2021 --species pear02 --raw

    # 2단계: 필드명 확인 후 정상 수집
    python fetch_pear_bloom_dates.py --service-key YOUR_KEY \
        --years 2020 2021 2022 2023 2024 2025 2026 \
        --out data/raw/pear_bloom_dates.csv
"""

import argparse
import time

import requests
import pandas as pd
import xml.etree.ElementTree as ET

BASE_URL = "https://apis.data.go.kr/1390804/Nihhs_Fruit_Pear_GrwhInfo/pearGrwnData"

# spciesCode: pear01=신고, pear02=원황 (화면에서 확인된 값)
DEFAULT_SPECIES_CODES = ["pear01", "pear02"]

# 확인된 실제 필드명 (2026-07-23, 실제 --raw 응답으로 검증 완료)
CONFIRMED_FIELDS = {
    "farm_code": "농장코드",
    "farm_name": "농장명",
    "ecln_datetm": "발아일자",
    "flblms_datetm": "만개일자",
}
# Body 레벨(농장별이 아니라 그 해 전체) 평균/평년 통계 필드
AGGREGATE_FIELDS = [
    "avgEclnDatetm", "avgFlblmsDatetm",      # 해당년도 평균
    "avgEclnDatetm2", "avgFlblmsDatetm2",    # 전년도 평균
    "avgEclnDatetmla", "avgFlblmsDatetmla",  # 평년(수년 평균) 평균 - MM-DD 형식
]


def fetch_raw(service_key, year, species_code):
    """원본 XML 텍스트를 그대로 반환 (필드명 확인용)."""
    params = {
        "serviceKey": service_key,
        "pageNo": 1,
        "numOfRows": 100,
        "selyear": year,
        "spciesCode": species_code,
    }
    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_item_generic(item_element):
    """item 안의 모든 자식 태그를 이름 그대로 dict로 뽑는다(필드명 확정 전 범용 파서)."""
    return {child.tag: child.text for child in item_element}


def fetch_year(service_key, year, species_code, num_of_rows=100):
    params = {
        "serviceKey": service_key,
        "pageNo": 1,
        "numOfRows": num_of_rows,
        "selyear": year,
        "spciesCode": species_code,
    }
    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    success = root.findtext(".//SuccessYN")
    if success != "Y":
        error_msg = root.findtext(".//ErrorMsg")
        print(f"  [{year}/{species_code}] 실패: {error_msg}")
        return []

    rows = []
    # item 태그를 못 찾을 수도 있어 여러 후보 경로를 시도
    items = root.findall(".//item")
    if not items:
        # 실제 확인된 구조: 농장별 레코드는 <Model> 태그 (item이 아님).
        # farm_code를 가진 부모 요소(=Model)를 찾는 방식으로 대응.
        items = root.findall(".//farm_code/..")

    # Body 레벨의 그 해 평균/전년평균/평년평균 통계(농장별이 아니라 전체 1세트)
    aggregates = {f: root.findtext(f".//{f}") for f in AGGREGATE_FIELDS}

    for item in items:
        row = parse_item_generic(item)
        row["year"] = year
        row["species_code"] = species_code
        row.update(aggregates)  # 모든 행에 그 해의 평균/평년 통계를 같이 붙여둔다
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description="배 발아/만개기 실측 데이터 수집")
    parser.add_argument("--service-key", required=True, help="data.go.kr 인증키")
    parser.add_argument("--year", type=int, help="--raw 모드에서 확인할 단일 연도")
    parser.add_argument("--years", nargs="+", type=int, help="정식 수집할 연도 목록")
    parser.add_argument("--species", nargs="*", default=DEFAULT_SPECIES_CODES)
    parser.add_argument("--out", default="pear_bloom_dates.csv")
    parser.add_argument("--raw", action="store_true", help="원본 XML을 그대로 출력하고 종료")
    args = parser.parse_args()

    if args.raw:
        year = args.year or (args.years[0] if args.years else 2021)
        species = args.species[0] if args.species else "pear02"
        print(f"=== 원본 XML 확인: {year}년 / {species} ===\n")
        raw_text = fetch_raw(args.service_key, year, species)
        print(raw_text)
        print("\n\n✅ 필드명이 이미 확인되었다면(CONFIRMED_FIELDS), --raw 없이 재실행하세요.")
        print("   새로운 연도에서 다른 필드가 나오면 이 옵션으로 다시 확인 가능합니다.")
        return

    if not args.years:
        parser.error("--raw가 아니면 --years가 필요합니다")

    all_rows = []
    for year in args.years:
        for species_code in args.species:
            print(f"수집 중: {year}년 / {species_code}")
            rows = fetch_year(args.service_key, year, species_code)
            all_rows.extend(rows)
            print(f"  -> {len(rows)}건")
            time.sleep(0.3)

    if not all_rows:
        print("\n⚠️ 수집된 데이터가 없습니다. --raw로 먼저 원본 응답을 확인하세요.")
        return

    df = pd.DataFrame(all_rows)
    df.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"\n완료: {args.out} ({len(df)}행, 컬럼: {list(df.columns)})")
    print(df.head(20).to_string(index=False))

    our_stations = {"영주", "안동", "문경", "거창", "천안", "광주"}
    if "farm_name" in df.columns:
        matched = set(df["farm_name"].dropna()) & our_stations
        print(f"\n우리 관측소와 겹치는 농장명: {matched if matched else '없음 (인근 지역으로 매칭 필요)'}")


if __name__ == "__main__":
    main()
