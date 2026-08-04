# -*- coding: utf-8 -*-
"""품종별 역병(疫病) 위험·대처 로더 (`data/late_blight.csv`).

이 데이터가 정직해서 그대로 살려야 하는 것 2가지
  1. **18품종 중 위험도가 실제로 기재된 것은 4개뿐이다.**
     감자 수미(높음)·자영(관리 필요)·대서(중간), 사과 홍로(명시·등급 없음).
     나머지 14품종은 `documented_in_dataset=N`이고 위험도 칸에 '확인 불가',
     '공통주의(개별 위험 미기재)' 같은 말이 들어 있다. 이걸 등급처럼 화면에 띄우면
     조사하지도 않은 위험을 판정한 것처럼 보인다 - `documented`로 갈라서 다루게 했다.

  2. **상추·배는 "역병 자료가 없다"가 정답이다.**
     CSV note가 짚어 준다 - 상추는 노균병·무름병·뿌리썩음병 자료가 있고 배는
     검은무늬병·검은별무늬병이 있지만, 그건 역병(Phytophthora)과 병원균이 다르다.
     있는 병해 자료를 역병인 것처럼 옮기면 농가가 엉뚱한 약제를 쓴다.

⚠️ 살균제 성분명은 내보내지 않는다.
   원본 사과 행의 management에 metalaxyl·cyazofamide·amisulbrome·cymoxanil·
   dimethomorph가 나열돼 있다. 약제는 **작물·병해별 등록이 다르고** 안전사용기준(희석
   배수·사용시기·횟수)이 붙는다. 초보에게 성분명만 던지면 미등록 약제를 쓰게 될 수 있다.
   chat_server의 시스템 프롬프트도 "농약 상품명이나 희석배수를 추천하지 않는다"를
   규칙으로 두고 있으므로, 데이터 계층에서 목록을 걷어내고 표준 안내로 바꾼다.
   (원문을 지우는 것이 아니라 내보내지 않는 것이다 - CSV에는 그대로 남아 있다.)
"""

import csv
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
CSV_PATH = BASE_DIR / "data" / "late_blight.csv"

# 등록 약제 확인 안내. 성분명 목록을 지운 자리에 넣는다.
PESTICIDE_NOTICE = ("등록 약제는 작물·병해별로 다르니 농약안전정보시스템에서 확인하고 "
                    "안전사용기준(희석배수·시기·횟수)을 지켜 쓰세요")

# 괄호 구간 전체를 먼저 잡고, 안쪽이 '영문 성분명 나열'인지 따로 판정한다.
# 문자 클래스로만 잡으려 했다가 실패했다 - 원본이
# "(metalaxyl·cyazofamide·amisulbrome·cymoxanil·dimethomorph 등)"처럼 한글 '등'을
# 섞어 쓰기 때문에 라틴 문자만 허용한 클래스가 매칭되지 않았다.
_PAREN = re.compile(r"\(([^)]*)\)")
# 약제 성분명은 관례상 전부 소문자로 적는다(metalaxyl, dimethomorph …).
# 병명·학명 병기는 첫 글자를 대문자로 쓴다(Late Blight, Phytophthora).
# 이 차이로 가른다 - 길이만으로 세면 '(疫病, Late Blight)'가 성분명으로 잡혀
# 병명 병기가 지워지고 엉뚱한 약제 안내가 붙는다(실제로 그렇게 틀렸다).
_LOWER_RUN = re.compile(r"(?<![A-Za-z])[a-z]{6,}(?![A-Za-z])")


def _is_ingredient_list(inner):
    """괄호 안이 약제 성분명 나열인가. 소문자 6자 이상 낱말이 2개 이상이면 그렇게 본다.

    임계값을 1로 낮추지 않은 이유: 학명의 종소명은 소문자다
    ("(Phytophthora cactorum)"의 cactorum). 1이면 학명 병기가 지워진다.
    대가로 성분이 하나만 적힌 경우("(metalaxyl 등)")는 통과한다 - 현재 데이터에는
    그런 행이 없다. 데이터가 갱신되면 이 판정을 다시 확인해야 한다.
    """
    return len(_LOWER_RUN.findall(inner)) >= 2


_cache = {}     # {"mtime": float, "rows": {(crop, variety): {...}}}


def _strip_ingredients(text):
    """관리 문구에서 살균제 성분명 나열을 걷어낸다. 지웠으면 표준 안내를 붙인다."""
    if not text:
        return text
    removed = False

    def repl(m):
        nonlocal removed
        if _is_ingredient_list(m.group(1)):
            removed = True
            return ""
        return m.group(0)

    cleaned = _PAREN.sub(repl, str(text))
    if not removed:
        return str(text).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned).replace(" ,", ",").strip()
    return f"{cleaned} — {PESTICIDE_NOTICE}"


def _load():
    """{(작물, 품종): 행} 형태. 파일 mtime이 바뀌면 다시 읽는다."""
    if not CSV_PATH.exists():
        return {}
    mtime = CSV_PATH.stat().st_mtime
    if _cache.get("mtime") == mtime:
        return _cache["rows"]
    rows = {}
    try:
        text = CSV_PATH.read_text(encoding="utf-8-sig")
        for r in csv.DictReader(text.splitlines()):
            crop = (r.get("crop") or "").strip()
            variety = (r.get("variety") or "").strip()
            if not crop or not variety:
                continue
            rows[(crop, variety)] = {
                "crop": crop,
                "variety": variety,
                "documented": (r.get("documented_in_dataset") or "").strip().upper() == "Y",
                "risk_raw": (r.get("risk_level") or "").strip(),
                "symptoms": (r.get("symptoms") or "").strip(),
                "management": _strip_ingredients(r.get("management")),
                "confidence": (r.get("confidence") or "").strip(),
                "source": (r.get("source") or "").strip(),
                "note": (r.get("note") or "").strip(),
            }
    except Exception as e:                                       # noqa: BLE001
        logger.error("[blight_data] %s 로드 실패: %s", CSV_PATH, e)
        return {}
    _cache.update(mtime=mtime, rows=rows)
    return rows


# 데이터에 없다는 뜻으로 쓰인 말들. 이 값이 risk_raw에 들어 있으면 등급으로 쓰지 않는다.
_NOT_ASSESSED = ("확인 불가", "미기재", "공통관리", "공통주의")


def blight_info(crop, variety):
    """품종 하나의 역병 정보. 없으면 None.

    반환
      documented   품종 자료에 역병 위험이 실제로 기재됐나
      risk         기재된 경우의 위험도(높음/관리 필요/중간/명시…). 미기재면 None
      status_text  화면·챗봇에 그대로 쓸 한 줄. 미기재면 그 사실을 밝힌다
      symptoms     증상(없으면 None)
      management   대처법. 살균제 성분명은 제거된 상태다
      confidence   원본이 밝힌 신뢰도
      note         병원균이 다르다는 등의 단서
    """
    row = _load().get((crop, variety))
    if not row:
        return None

    raw = row["risk_raw"]
    assessed = row["documented"] and raw and not any(h in raw for h in _NOT_ASSESSED)
    risk = raw if assessed else None

    # status_text 는 한 줄 설명, badge_text 는 알약 배지에 넣는 **짧은** 라벨이다.
    # 둘을 하나로 쓰다가 배지 안에 긴 문장이 줄바꿈되어 들어가는 꼴이 났다.
    if assessed:
        status = f"역병 위험 {risk}"
        # 홍로의 risk 는 '명시(수치 등급 없음)'처럼 길다 - 등급어일 때만 배지에 싣는다.
        badge = f"위험 {risk}" if risk in ("높음", "중간", "낮음", "관리 필요") else "관리 대상"
    elif row["documented"]:
        # 기재는 됐지만 등급이 아닌 경우(사과 홍로: '명시(수치 등급 없음)')
        status = "품종 자료에 역병이 관리 대상으로 적혀 있어요 (위험 등급은 없어요)"
        badge = "관리 대상"
    else:
        status = "이 품종의 역병 저항성 자료가 없어요 — 작물 공통 예방 원칙을 따르세요"
        badge = "자료 없음"

    def clean(v):
        return None if not v or v in ("확인 불가", "데이터에 없음",
                                      "데이터에 별도 증상 기재 없음") else v

    return {
        "documented": row["documented"],
        "assessed": bool(assessed),
        "risk": risk,
        "risk_raw": raw or None,
        "status_text": status,
        "badge_text": badge,
        "symptoms": clean(row["symptoms"]),
        "management": clean(row["management"]),
        "confidence": row["confidence"] or None,
        "source": clean(row["source"]),
        "note": row["note"] or None,
    }


def available(crop=None):
    """역병 자료가 있는 (작물, 품종) 목록."""
    keys = sorted(_load())
    return [k for k in keys if crop is None or k[0] == crop]


def assessed_varieties(crop=None):
    """위험도가 **실제로 기재된** 품종만. 화면에서 '자료 있음'을 셀 때 쓴다."""
    out = []
    for c, v in available(crop):
        info = blight_info(c, v)
        if info and info["assessed"]:
            out.append((c, v))
    return out
