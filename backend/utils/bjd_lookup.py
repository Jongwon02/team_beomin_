"""행정구역명(시군구) -> 법정동코드(STDG_CD, 10자리) 변환.

흙토람 농경지화학성 통계정보 API(SoilExamStat V2)는 STDG_CD(10자리, 국토교통부
법정동코드)를 요청 파라미터로 받는다. region_mapper.py가 쓰는 sigungu_coordinates.json
좌표는 이 코드 체계와 무관하므로, 별도로 data/raw/bjd_code.csv(국토교통부
법정동코드 전체 목록, 2025-08-05 기준)를 두고 이름으로 조회한다.

⚠️ 2023~2024년 지자체명 변경(강원도->강원특별자치도 등) 이후 CSV에는 신/구 명칭이
   모두 남아있고, 신 명칭 행만 "존재"로 표시된다("폐지"행은 API에서 데이터가 없음을
   실측으로 확인함 - 예: "강원도 평창군"(폐지, 4276000000)은 데이터 없음,
   "강원특별자치도 평창군"(존재, 5176000000)만 유효). 따라서 "존재" 행만 사용한다.

⚠️ 천안시처럼 하위 구가 있는 시는 시 자체 코드(예: 4413000000)로 조회하면
   "요청 데이터 없음"이 반환됨을 실측으로 확인했다(구 단위 통계만 존재). 이런 경우
   하위 구 코드 목록을 candidates로 반환해 호출부(soil.py)가 구별로 받아 평균내게 한다.
"""

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]  # farm-guide/
BJD_CODE_CSV_PATH = BASE_DIR / "data" / "raw" / "bjd_code.csv"

SIDO_RENAME_MAP = {
    "강원특별자치도": "강원도",
    "전북특별자치도": "전라북도",
}


def _canonicalize_old(name):
    """신 명칭 -> 구 명칭 (region_mapper.py와 동일 규칙)."""
    for new_name, old_name in SIDO_RENAME_MAP.items():
        if name.startswith(new_name):
            return old_name + name[len(new_name):]
    return name


_df_cache = None


def _load_df(path=BJD_CODE_CSV_PATH):
    global _df_cache
    if _df_cache is not None:
        return _df_cache
    df = pd.read_csv(path, dtype={"법정동코드": str}, encoding="utf-8")
    df = df[df["폐지여부"] == "존재"].copy()
    code_int = df["법정동코드"].astype("int64")
    df["시군구_level"] = (code_int % 100000 == 0) & (code_int % 100000000 != 0)
    _df_cache = df
    return df


def _matches(row_name, target):
    return row_name == target or _canonicalize_old(row_name) == target or row_name == _canonicalize_old(target)


def get_stdg_candidates(sigungu_full_name, path=BJD_CODE_CSV_PATH):
    """"강원도 평창군" 같은 "시도 시군구" 전체명을 받아 STDG_CD 후보 목록을 반환한다.

    반환: {"exact": "5176000000" 또는 None, "children": ["4413100000", "4413300000", ...]}
    - exact: 그 시군구 자체의 법정동코드(존재하면).
    - children: 하위 구가 있는 시일 때, 그 구들의 법정동코드 목록(없으면 빈 리스트).
    호출부(soil.py)는 children이 있으면 그걸 우선 쓰고(시 자체 코드는 데이터가 없는
    경우가 많음), 없으면 exact를 쓴다.
    """
    df = _load_df(path)
    target = sigungu_full_name.strip()

    sigungu_rows = df[df["시군구_level"]]
    exact_rows = sigungu_rows[sigungu_rows["법정동명"].map(lambda n: _matches(n, target))]
    if len(exact_rows) == 0:
        logger.warning("[bjd_lookup] '%s'에 해당하는 법정동코드를 찾을 수 없습니다.", sigungu_full_name)
        return {"exact": None, "children": []}

    exact_row = exact_rows.iloc[0]
    exact_code = exact_row["법정동코드"]
    real_name = exact_row["법정동명"]  # CSV에 실제 존재하는 신/구 명칭 그대로(구 매칭 접두어로 사용)

    # 구가 있는 시(천안시 동남구/서북구 등)는 코드가 "시군구" 3자리 segment 자체가
    # 다른 값이라 코드 접두어로는 부모-자식 관계를 알 수 없다 - 이름 접두어로 찾는다.
    child_rows = sigungu_rows[
        sigungu_rows["법정동명"].str.startswith(real_name + " ")
    ]
    children = sorted(child_rows["법정동코드"].tolist())

    return {"exact": exact_code, "children": children}
