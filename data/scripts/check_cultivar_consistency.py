# -*- coding: utf-8 -*-
"""품종 데이터(L2)와 품종 리포트(L1)의 수치가 어긋나지 않는지 검사한다. (breed.md §2)

왜 필요한가
  사람이 쓴 리포트에는 "약 80~90일", 데이터 파일에는 {"min":80,"max":90}이 들어 있다.
  둘 중 하나만 고치면 화면 비교표와 리포트 본문이 다른 숫자를 말하게 되고, 챗봇은
  둘 다 근거로 인용한다. breed.md §14가 최상위 리스크로 꼽은 상황이라 사람의 주의력에
  맡기지 않고 스크립트로 막는다.

실행
  python data/scripts/check_cultivar_consistency.py
  종료코드 0 = 이상 없음 / 1 = 불일치 발견 (경고만이면 0)
"""

import json
import re
import sys
from pathlib import Path

# 윈도우 콘솔(cp949)에서 한글 결과가 깨지지 않게 한다.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

BASE_DIR = Path(__file__).resolve().parents[2]
CULTIVAR_DIR = BASE_DIR / "data" / "cultivars"
REPORT_DIR = BASE_DIR / "data" / "cultivar_reports"
CROP_STANDARDS = BASE_DIR / "crop_standards_v2.json"


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _reports_for(crop):
    """작물 리포트 파일들. 반환 {파일명: 본문}.

    ⚠️ '본문에 품종명이 있는가'로 대조 대상을 정하면 안 된다 - 추백·자영 리포트 본문에는
       비교 서술로 '수미'가 등장하므로, 수미가 자기 것이 아닌 리포트와 대조되어 검사가
       헛통과한다(실제로 그랬다). 대조 대상은 **파일명**으로만 정한다
       (data/cultivar_reports/<작물>_<품종>-<품종>.md — cultivar_data._report_path와 같은 규칙).
    """
    if not REPORT_DIR.exists():
        return {}
    return {p.name: (p.stem, p.read_text(encoding="utf-8"))
            for p in sorted(REPORT_DIR.glob(f"{crop}_*.md"))}


def _norm(text):
    """숫자 비교용 정규화: 공백·물결표 표기를 통일한다."""
    return re.sub(r"\s+", "", text).replace("〜", "~").replace("～", "~")


def check_crop(crop, problems, warnings):
    data = _load(CULTIVAR_DIR / f"{crop}.json")
    reports = _reports_for(crop)
    std = _load(CROP_STANDARDS).get(crop, {})

    for v in data.get("varieties") or []:
        name = v.get("name_ko") or v.get("id")
        # 이 품종을 '다루는' 리포트(파일명에 품종명이 들어간 것)만 대조 대상이다.
        mine = [body for (stem, body) in reports.values() if name in stem]
        if not mine:
            warnings.append(f"{crop} '{name}': 대조할 리포트가 없습니다(품종 데이터만 존재)")
            continue
        joined = _norm("\n".join(mine))

        # ① 생육일수가 리포트 본문에 그대로 등장하는가
        gd = v.get("growth_period_days") or {}
        spans = []
        if gd.get("min") is not None:
            spans.append((gd.get("min"), gd.get("max")))
        for key in ("spring", "summer", "fall", "autumn"):
            if isinstance(gd.get(key), dict):
                spans.append((gd[key].get("min"), gd[key].get("max")))
        for lo, hi in spans:
            if lo is None:
                continue
            # 'f"{lo}일"' 단독 표기는 후보에서 뺀다 - 다른 문장의 숫자에 우연히 걸려
            # (예: '100일까지 연장') 검사가 헛통과한다.
            candidates = [f"{lo}~{hi}일", f"{lo}일이상", f"약{lo}~{hi}일", f"{lo}일이상의"]
            if not any(c in joined for c in candidates):
                problems.append(
                    f"{crop} '{name}': 생육일수 {lo}~{hi}일이 리포트 본문에 없습니다"
                    f" (찾은 표기: {candidates[:2]})"
                )

        # ② 토양산도가 품종 파일에 있으면 리포트에도 같은 값이 있어야 한다
        ph = (v.get("recommended_environment") or {}).get("soil_ph")
        if isinstance(ph, dict) and ph.get("min") is not None:
            if f"pH{ph['min']}~{ph['max']}" not in joined:
                problems.append(f"{crop} '{name}': pH {ph['min']}~{ph['max']}가 리포트에 없습니다")

        # ③ 비대 적온이 작물표준과 다르면(품종이 덮어쓰는 경우) 그 사실을 남긴다.
        #    폴백 값과 다른 것은 오류가 아니지만, 어느 쪽을 쓰는지 모른 채 방치하면 안 된다.
        bulk = (v.get("recommended_environment") or {}).get("tuber_bulking_temperature_c")
        std_bulk = ((std.get("temperature") or {}).get("tuber_bulking_optimal") or {})
        if isinstance(bulk, dict) and bulk.get("min") is not None and std_bulk.get("min") is not None:
            if (bulk["min"], bulk["max"]) != (std_bulk["min"], std_bulk["max"]):
                warnings.append(
                    f"{crop} '{name}': 비대 적온 {bulk['min']}~{bulk['max']}℃가 작물표준"
                    f" {std_bulk['min']}~{std_bulk['max']}℃와 다릅니다(품종값을 씁니다)"
                )

        # ④ 판매·표시 안전장치: 기능성 성분이 있는 품종은 '함량 단정 금지' 문구가 있어야 한다
        comp = (v.get("tuber_characteristics") or {}).get("special_component")
        if comp:
            warns = _norm(" ".join(v.get("key_warnings") or []))
            if "검사" not in warns and "성분검사" not in joined:
                problems.append(
                    f"{crop} '{name}': 기능성 성분({comp})을 다루는데 '검사 없이 함량·효능을"
                    f" 표시하지 말라'는 주의가 데이터·리포트 어디에도 없습니다"
                )

    # ⑤ 데이터 제공자 주의문(파종일·시비량은 지역에 따라 다르다 등)이 있는지
    if not (data.get("dataset") or {}).get("caution"):
        problems.append(f"{crop}: dataset.caution(주의문)이 비어 있습니다")


def main():
    if not CULTIVAR_DIR.exists():
        print(f"품종 데이터 폴더가 없습니다: {CULTIVAR_DIR}")
        return 1

    problems, warnings = [], []
    crops = sorted(p.stem for p in CULTIVAR_DIR.glob("*.json"))
    if not crops:
        print("검사할 품종 파일이 없습니다.")
        return 1

    for crop in crops:
        check_crop(crop, problems, warnings)

    for w in warnings:
        print(f"[경고] {w}")
    for p in problems:
        print(f"[불일치] {p}")

    print(f"\n검사한 작물: {', '.join(crops)} · 불일치 {len(problems)}건 · 경고 {len(warnings)}건")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
