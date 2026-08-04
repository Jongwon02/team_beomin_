# -*- coding: utf-8 -*-
"""작물 일반 지식 로더 (`data/crops_for_llm.json` · 농촌진흥청 농업기술길잡이 5권).

무엇을 담고 있나
  5작물(사과·배·오이·상추·감자)에 대해 15개 필드가 균일하게 들어 있다. 원문 수치를
  그대로 보존한 자료이고, 출처 문서명(`source_document`)도 작물마다 실려 있다.

왜 토픽으로 잘라 주는가
  전체가 212KB다. 그대로 도구 응답에 실으면 컨텍스트가 터지고 비용도 감당이 안 된다.
  게다가 필드 하나가 통째로 큰 경우가 있다 - 오이 `physiological_disorders`는 10,968자
  (27건), `pests_and_diseases`는 8,357자(25건)다. 그래서 질문에 해당하는 토픽만 잘라
  넘기고, 목록형 필드는 건수를 제한한 뒤 **몇 건을 줄였는지 함께 알린다**(조용히 자르면
  챗봇이 "이게 전부"라고 답한다).

⚠️ `major_varieties`는 일부러 내보내지 않는다.
  이 필드에는 감자만 24품종이 들어 있다(농진청이 정리한 '국내에 이런 품종이 있다'는
  배경지식). 우리가 특성을 검수해 **추천할 수 있는 품종은 `data/cultivars/`의 18품종뿐**
  이고, 추천은 `get_cultivar_candidates`/`get_cultivar_profile`이 담당한다. 이 목록이
  챗봇에 흘러들면 검수하지 않은 품종을 권하게 되므로 로더 단계에서 끊는다
  (프롬프트로 "쓰지 마라"고 부탁하는 것보다 구조로 막는 편이 확실하다).
  품종 목록 질문에는 `cultivar_data.variety_names()`가 답해야 한다.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_PATH = BASE_DIR / "data" / "crops_for_llm.json"

# 원문은 작물을 영문 키로 담는다. 앱 전체가 한글 작물명을 쓰므로 여기서 잇는다.
CROP_KEY = {"사과": "apple", "배": "pear", "오이": "cucumber",
            "상추": "lettuce", "감자": "potato"}

# 토픽 → 원문 필드. 어휘는 cultivar_data 쪽 CULTIVAR_TOPICS와 결이 같게 맞췄다
# (화면·챗봇에서 두 벌의 토픽 어휘가 섞이지 않게).
TOPIC_FIELDS = {
    "개요": ("overview",),
    "생육특성": ("growth_characteristics",),
    "재배환경": ("cultivation_environment",),
    "작형캘린더": ("cultivation_types_and_calendar",),
    "재배관리": ("cultivation_management",),
    "병해충": ("pests_and_diseases",),
    "생리장해": ("physiological_disorders",),
    "수확저장": ("harvest_and_storage",),
    "기타": ("additional_notes",),
}
TOPICS = tuple(TOPIC_FIELDS)

# 축약 한도.
MAX_LIST_ITEMS = 6          # 목록형 필드에서 넘길 건수
MAX_ITEM_CHARS = 320        # 목록 항목의 개별 문자열 길이
MAX_TEXT_CHARS = 1500       # 서술형 필드 길이(1차 시도값)

# 토픽 하나의 응답 상한. 필드 모양이 작물마다 달라서 항목별 한도만으로는 못 막는다 -
# 오이 `cultivation_management`는 값이 여러 개인 dict라 값마다 1,500자를 허용하면
# 5,400자(≈2,700토큰)까지 불어난다. 그래서 만든 뒤 크기를 재고, 넘으면 한도를 조여
# 다시 만든다(_fit_budget). 상한을 넘긴 채로 내보내지 않는다.
MAX_TOPIC_CHARS = 2600
_TEXT_LIMIT_STEPS = (1500, 900, 550, 320, 180)

# 목록 항목에서 이름 노릇을 하는 키(앞에 세워 보여준다)
_NAME_KEYS = ("name", "type", "stage", "disorder")

_cache = {}                 # {"mtime": float, "data": {...}}


def _load():
    """원본 JSON. 파일 mtime이 바뀌면 다시 읽는다(서버 재시작 없이 데이터 교체 가능)."""
    if not DATA_PATH.exists():
        return None
    mtime = DATA_PATH.stat().st_mtime
    if _cache.get("mtime") == mtime:
        return _cache["data"]
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except Exception as e:                                       # noqa: BLE001
        logger.error("[crop_knowledge] %s 로드 실패: %s", DATA_PATH, e)
        return None
    _cache.update(mtime=mtime, data=data)
    return data


def available_crops():
    """지식 데이터가 있는 한글 작물명."""
    data = _load()
    if not data:
        return []
    crops = data.get("crops") or {}
    return [ko for ko, en in CROP_KEY.items() if en in crops]


def usage_guidelines():
    """데이터 제공자가 붙인 사용 주의문. 답변에 함께 실어야 하는 문구다."""
    data = _load()
    if not data:
        return []
    return (data.get("dataset_info") or {}).get("usage_guidelines") or []


def _clip(text, limit):
    """길면 자르고 잘렸음을 표시한다. 조용히 자르지 않는다."""
    s = str(text).strip()
    return s if len(s) <= limit else s[:limit].rstrip() + f"…(이하 생략, 원문 {len(s)}자)"


def _shrink_item(item):
    """목록 항목 1건을 축약한다. 이름 키를 앞에 세우고 나머지 문자열을 자른다."""
    if not isinstance(item, dict):
        return _clip(item, MAX_ITEM_CHARS)
    out = {}
    for k in _NAME_KEYS:
        if item.get(k):
            out[k] = str(item[k]).strip()
    for k, v in item.items():
        if k in out:
            continue
        if isinstance(v, str) and v.strip():
            out[k] = _clip(v, MAX_ITEM_CHARS)
        elif isinstance(v, list) and v:
            out[k] = [_clip(x, MAX_ITEM_CHARS) for x in v[:3]]
        elif isinstance(v, dict) and v:
            out[k] = {kk: _clip(vv, MAX_ITEM_CHARS) for kk, vv in list(v.items())[:4]}
    return out


def _shrink(value, text_limit=MAX_TEXT_CHARS):
    """필드 하나를 축약한다. (축약값, 생략건수)"""
    if isinstance(value, list):
        kept = [_shrink_item(x) for x in value[:MAX_LIST_ITEMS]]
        return kept, max(0, len(value) - MAX_LIST_ITEMS)
    if isinstance(value, dict):
        return {k: _clip(v, text_limit) if isinstance(v, str) else v
                for k, v in value.items()}, 0
    return _clip(value, text_limit), 0


def _fit_budget(build):
    """build(text_limit)를 점점 조여 호출해 MAX_TOPIC_CHARS 안에 들어오는 결과를 준다.

    가장 조인 값에서도 넘치면 그 결과를 그대로 준다 - 여기서 더 자르면 문장이 뜻을
    잃는다. 대신 크기를 응답에 밝혀 호출부가 상황을 알 수 있게 한다.
    """
    out = None
    for limit in _TEXT_LIMIT_STEPS:
        out = build(limit)
        if len(json.dumps(out, ensure_ascii=False)) <= MAX_TOPIC_CHARS:
            return out
    return out


def crop_info(crop, topic=None):
    """작물 일반 지식. topic을 주면 그 토픽만, 없으면 개요 + 토픽 목록.

    반환은 챗봇 도구가 그대로 실어 보낼 수 있는 형태다. 실패는 '조회실패' 키로 알린다
    (chat_server의 다른 도구와 같은 규약).
    """
    data = _load()
    if not data:
        return {"조회실패": "작물 지식 데이터를 읽지 못했어요 (data/crops_for_llm.json)"}

    en = CROP_KEY.get(crop)
    if not en:
        return {"조회실패": f"지원하지 않는 작물이에요: {crop}",
                "가능한작물": list(CROP_KEY)}
    cd = (data.get("crops") or {}).get(en)
    if not cd:
        return {"조회실패": f"'{crop}' 지식 데이터가 아직 없어요",
                "가능한작물": available_crops()}

    head = {
        "작물": cd.get("crop_name_kr") or crop,
        "학명": cd.get("scientific_name"),
        "분류": cd.get("family"),
        "출처": cd.get("source_document"),
    }

    if not topic:
        return {**head,
                "개요": _clip(cd.get("overview") or "", MAX_TEXT_CHARS),
                "더볼수있는토픽": list(TOPICS),
                "주의": usage_guidelines()[:1]}

    fields = TOPIC_FIELDS.get(topic)
    if not fields:
        return {"조회실패": f"'{topic}'은 없는 토픽이에요", "가능한토픽": list(TOPICS)}

    if all(cd.get(f) in (None, "", [], {}) for f in fields):
        return {**head, "조회실패": f"'{crop}'에는 '{topic}' 자료가 없어요",
                "가능한토픽": list(TOPICS)}

    guide = usage_guidelines()

    def build(text_limit):
        body, omitted = {}, {}
        for f in fields:
            v = cd.get(f)
            if v in (None, "", [], {}):
                continue
            shrunk, cut = _shrink(v, text_limit)
            body[topic] = shrunk
            if cut:
                omitted[f] = cut
        out = {**head, **body}
        if omitted:
            # 몇 건을 줄였는지 밝힌다. 이게 없으면 챗봇이 6건을 전부라고 답한다.
            out["생략된건수"] = omitted
            out["생략안내"] = ("자료가 많아 앞 %d건만 보냈어요. 더 필요하면 어떤 항목인지 "
                               "물어보라고 안내하세요." % MAX_LIST_ITEMS)
        if guide:
            out["주의"] = guide[1:2] if topic == "병해충" and len(guide) > 1 else guide[:1]
        return out

    return _fit_budget(build)
