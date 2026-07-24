"""
국립원예특작과학원(NIHHS) 과수생육품질관리시스템 - 사과 만개기 실측 데이터 수집.

⚠️ 이 스크립트는 외부 네트워크(apis.data.go.kr) 접속이 필요합니다.
   Claude Code나 로컬 터미널에서 실행하세요 (이 대화창의 샌드박스에서는 실행 불가).

data.go.kr에서 "농촌진흥청 국립원예특작과학원_사과생육품질정보" API를 신청하고
받은 서비스키(디코딩된 키 권장 - 배 API 때 확인된 것과 같은 방식)를 --service-key로 넘기세요.

⚠️ 배 API 때 문서 스펙(response element 표)과 실제 응답이 달랐던 적이 있어서(발아일자
   필드명이 문서에 없었음), 사과도 동일한 위험이 있습니다. 반드시 --raw로 먼저
   원본 응답을 확인한 뒤 본 수집으로 넘어가세요.

사용법:
    # 1단계: 원본 XML 확인 (반드시 먼저 실행)
    python fetch_apple_bloom_dates.py --service-key YOUR_KEY --year 2021 --species apple01 --raw

    # 2단계: 확인 후 정상 수집
    python fetch_apple_bloom_dates.py --service-key YOUR_KEY \
        --years 2020 2021 2022 2023 2024 2025 2026 \
        --out data/raw/apple_bloom_dates.csv
"""

import argparse
import time

import requests
import pandas as pd
import xml.etree.ElementTree as ET

BASE_URL = "http://apis.data.go.kr/1390804/Nihhs_Fruit_GrwhInfo/appleGrwnDataList"

# ⚠️ 확인 필요: 이 API의 품종코드(spciesCode) 종류를 실제로 모른다(문서 샘플엔 "apple01"
# 하나만 나와 있음). --raw로 먼저 확인하고, 필요하면 --species로 다른 코드도 시도.
DEFAULT_SPECIES_CODES = ["apple01"]

# 문서에서 확인된 필드(참고용 - 실제 --raw 응답으로 재검증 필요)
DOCUMENTED_FIELDS = {
    "farm_code": "농장코드",
    "farm_name": "농장명",
    "flblms_datetm": "만개일자",
}
# 배 API에서 발견됐던 것과 같은 패턴의 발아일자 필드가 있을 수 있어 후보로 같이 찾아본다
POSSIBLE_EXTRA_FIELDS = ["ecln_datetm", "germn_datetm", "atrs_datetm"]
AGGREGATE_FIELD_CANDIDATES = [
    "avgFlblmsDatetm", "avgFlblmsDatetm2", "avgFlblmsDatetmla",
    "avgEclnDatetm", "avgEclnDatetm2", "avgEclnDatetmla",
]


def fetch_raw(service_key, year, species_code):
    """원본 XML 텍스트를 그대로 반환 (필드명 확인용)."""
    params = {
        "serviceKey": service_key,
        "pageNo": 1,
        "numOfRows": 100,
        "selyear": year,
        "frtgrdCode": "apple",
        "spciesCode": species_code,
    }
    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_item_generic(item_element):
    """item/Model 안의 모든 자식 태그를 이름 그대로 dict로 뽑는다."""
    return {child.tag: child.text for child in item_element}


def fetch_year(service_key, year, species_code, num_of_rows=100):
    params = {
        "serviceKey": service_key,
        "pageNo": 1,
        "numOfRows": num_of_rows,
        "selyear": year,
        "frtgrdCode": "apple",
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

    # 배 API 경험상 레코드가 <item>이 아니라 <Model> 등 다른 태그일 수 있어 범용으로 찾는다
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//farm_code/..")

    aggregates = {f: root.findtext(f".//{f}") for f in AGGREGATE_FIELD_CANDIDATES
                  if root.findtext(f".//{f}") is not None}

    rows = []
    for item in items:
        row = parse_item_generic(item)
        row["year"] = year
        row["species_code"] = species_code
        row.update(aggregates)
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description="사과 만개기 실측 데이터 수집")
    parser.add_argument("--service-key", required=True, help="data.go.kr 인증키")
    parser.add_argument("--year", type=int, help="--raw 모드에서 확인할 단일 연도")
    parser.add_argument("--years", nargs="+", type=int, help="정식 수집할 연도 목록")
    parser.add_argument("--species", nargs="*", default=DEFAULT_SPECIES_CODES)
    parser.add_argument("--out", default="apple_bloom_dates.csv")
    parser.add_argument("--raw", action="store_true", help="원본 XML을 그대로 출력하고 종료")
    args = parser.parse_args()

    if args.raw:
        year = args.year or (args.years[0] if args.years else 2021)
        species = args.species[0] if args.species else "apple01"
        print(f"=== 원본 XML 확인: {year}년 / {species} ===\n")
        raw_text = fetch_raw(args.service_key, year, species)
        print(raw_text)
        print("\n\n⚠️ 위 원본 XML에서 만개일자(및 혹시 발아일자) 실제 태그명을 확인하세요.")
        print("   DOCUMENTED_FIELDS/POSSIBLE_EXTRA_FIELDS와 다르면 fetch_year()의")
        print("   파싱 로직을 그 필드명에 맞게 수정한 뒤 --raw 없이 재실행하세요.")
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
        print(f"\n우리 관측소와 겹치는 농장명: {matched if matched else '없음 (인근 지역으로 매핑 필요)'}")


if __name__ == "__main__":
    main()
