# -*- coding: utf-8 -*-
"""법정동코드 즉시 조회 모듈.

국토교통부_법정동코드 CSV(cp949)를 읽어 '이름 -> 10자리 법정동코드'를 조회한다.
폐지된 코드는 기본 제외. 토양/기상 API의 STDG_CD 파라미터에 바로 사용 가능.

CLI:
    python bjd_lookup.py 충주 칠금동
    python bjd_lookup.py 강릉시
"""
import csv, os, sys, functools

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "국토교통부_법정동코드_20250805.csv")


@functools.lru_cache(maxsize=1)
def _load(include_abolished=False):
    """CSV를 한 번만 읽어 [(code, name, alive)] 리스트로 캐시."""
    rows = []
    with open(CSV_PATH, encoding="cp949", newline="") as f:
        reader = csv.reader(f)
        next(reader)  # 헤더: 법정동코드,법정동명,폐지여부
        for r in reader:
            if len(r) < 3:
                continue
            code, name, status = r[0].strip(), r[1].strip(), r[2].strip()
            alive = ("폐지" not in status)  # '존재' / '폐지'
            if alive or include_abolished:
                rows.append((code, name, alive))
    return rows


def find(*keywords, alive_only=True, limit=20):
    """모든 keyword를 법정동명에 포함하는 항목을 반환.

    예) find("충주", "칠금동") -> [("4313011600", "충청북도 충주시 칠금동", True)]
    """
    kws = [k for k in keywords if k]
    out = []
    for code, name, alive in _load(include_abolished=not alive_only):
        if all(k in name for k in kws):
            out.append((code, name, alive))
    # 이름이 짧을수록(상위 행정구역) 먼저 오도록 정렬
    out.sort(key=lambda x: len(x[1]))
    return out[:limit]


def code_of(*keywords):
    """가장 잘 맞는 1건의 코드만 반환(없으면 None). 완전일치 우선."""
    matches = find(*keywords)
    if not matches:
        return None
    full = " ".join(keywords)
    for code, name, _ in matches:
        if name == full or name.endswith(full):
            return code
    return matches[0][0]


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("사용법: python bjd_lookup.py <검색어...>  예) python bjd_lookup.py 충주 칠금동")
        sys.exit(0)
    sys.stdout.reconfigure(encoding="utf-8")
    res = find(*args)
    if not res:
        print(f"'{' '.join(args)}' 검색 결과 없음")
    else:
        for code, name, alive in res:
            print(f"{code}\t{name}")
