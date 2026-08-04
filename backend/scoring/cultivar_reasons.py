# -*- coding: utf-8 -*-
"""품종별 추천 근거(pros) · 고려할 점(cons) 조립.

왜 따로 모듈로 뺐나
  기후 채점(cultivar_fit·감자)과 조건 매칭(cultivar_conditions·나머지 4작물)이 서로 다른
  엔진인데, 화면에 나가는 "왜 이 품종인가"는 같은 형식이어야 한다. 두 곳에 각각 쓰면
  한쪽만 고쳐지고 문구가 갈린다.

원칙 — 문장을 새로 쓰지 않는다
  pros·cons의 근거는 전부 데이터에 적힌 값이다. 우리가 하는 일은 그 값을 사람이 읽을
  문장으로 옮기고 **어디서 나온 값인지(basis)를 함께 붙이는 것**뿐이다. basis가 없으면
  사용자는 "왜 이게 장점인지"를 확인할 방법이 없고, 그러면 납득이 아니라 신뢰 요구가 된다.

  없는 근거를 만들지 않는다. 예를 들어 사과는 `disease_and_pest_risks`가 없어서 병해
  감수성을 cons에 넣을 수 없다 - 대신 그 작물이 실제로 가진 `risks`를 쓴다.

작물마다 있는 필드가 다르다(실측)
  key_warnings          5작물 전부
  selection_conditions  배·오이·상추·감자 (사과는 없음)
  beginner_friendly     오이·상추·감자 (사과·배는 없음)
  disease_and_pest_risks 감자·상추 전부, 배 2/3 (사과는 없음)
  risks                 사과만
  specific_risks        오이만
  storage               사과·상추·감자
"""

import logging

logger = logging.getLogger(__name__)

MAX_PROS = 4
MAX_CONS = 4

# 저장성 서술 중 '장점'으로 볼 수 있는 등급
_GOOD_STORAGE = ("매우 높음", "높음", "강")


def with_particle(word, has_final="으로", no_final="로"):
    """받침 유무에 맞는 조사를 붙인다. "봄재배으로" 같은 오타를 막는다.

    한글 음절은 U+AC00부터 초성×588 + 중성×28 + 종성 순으로 배열되므로,
    (코드포인트 - 0xAC00) % 28 == 0 이면 종성(받침)이 없다.
    'ㄹ' 받침은 '으로'가 아니라 '로'를 쓴다(예: 서울로).
    """
    if not word:
        return word
    last = word[-1]
    if not ("가" <= last <= "힣"):
        return f"{word}{no_final}"          # 숫자·영문은 판단하지 않고 짧은 쪽
    jong = (ord(last) - 0xAC00) % 28
    if jong == 0 or jong == 8:              # 받침 없음 또는 'ㄹ'
        return f"{word}{no_final}"
    return f"{word}{has_final}"


def _item(text, basis):
    """근거 1건. text는 화면 문장, basis는 어느 데이터에서 왔는지."""
    text = (text or "").strip()
    return {"text": text, "basis": basis} if text else None


def _add(bucket, item):
    if item and not any(x["text"] == item["text"] for x in bucket):
        bucket.append(item)


def _texts(value, limit=3):
    """문자열/리스트/딕트를 문장 리스트로."""
    out = []
    if isinstance(value, str):
        if value.strip():
            out.append(value.strip())
    elif isinstance(value, list):
        for v in value[:limit]:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
            elif isinstance(v, dict):
                t = v.get("condition") or v.get("reason") or v.get("name")
                if t:
                    out.append(str(t).strip())
    return out


def _storage_pro(variety):
    """저장성이 좋으면 장점으로. 며칠까지 되는지 숫자를 함께 보여준다."""
    st = variety.get("storage") or {}
    ability = st.get("ability")
    if not ability or not any(g in str(ability) for g in _GOOD_STORAGE):
        return None
    cold = st.get("cold_storage_days_approx")
    room = st.get("room_temperature_days_approx")
    detail = (f" (냉장 약 {cold}일)" if cold else (f" (상온 약 {room}일)" if room else ""))
    return _item(f"저장성 '{ability}' — 수확 후 나눠 팔 수 있어요{detail}", "품종자료 저장")


def _use_pro(variety):
    uses = _texts(variety.get("primary_use"), 3)
    if not uses:
        return None
    return _item("주로 " + " · ".join(uses) + " 용도예요", "품종자료 용도")


def _beginner(variety, experience):
    """초보 적합/부적합. 점수에는 넣지 않고 근거로만 쓴다(breed.md §6.5).

    ⚠️ `beginner_friendly`가 None이면 **데이터에 그 항목이 없다**는 뜻이다(사과·배).
       False와 구분해야 한다 - 예전에 bool(None)=False로 뭉개서 사과 5품종 전부에
       "초보자에게는 손이 많이 가요"가 근거 없이 붙었다.
    """
    flag = variety.get("beginner_friendly")
    if flag is None:
        return None, None
    reason = variety.get("beginner_reason")
    if flag:
        text = "초보자가 다루기 무난해요" + (f" — {reason}" if reason else "")
        return _item(text, "품종자료 초보적합"), None
    text = "초보자에게는 손이 많이 가요" + (f" — {reason}" if reason else "")
    return None, _item(text, "품종자료 초보적합")


def _disease_cons(variety, skip_names=()):
    """위험도 '높음'으로 적힌 병해충만 cons에 올린다. 중간·낮음은 카드를 채우기만 한다.

    skip_names 는 이미 다른 근거로 말한 병해다. 역병은 별도 자료(blight_data)로
    대처법까지 붙여 먼저 말하므로, 여기서 또 "역병에 약해요"를 넣으면 같은 말이 두 줄
    나간다 - 카드에 4줄만 보이는데 그 중 둘이 같으면 다른 위험을 못 보여준다.
    """
    out = []
    for d in variety.get("diseases") or []:
        name = d.get("name")
        if d.get("level") != "높음" or not name or name in skip_names:
            continue
        out.append(_item(f"{name}에 약해요 (위험 높음)", "품종자료 병해충"))
    return out


def _blight_items(blight):
    """역병 정보를 cons(또는 안내)로. 조사되지 않은 위험을 등급처럼 쓰지 않는다."""
    if not blight:
        return []
    if blight.get("assessed") and blight.get("risk"):
        # 위험도 값이 '높음'·'중간'·'관리 필요'처럼 명사라 "…이에요"를 그냥 붙이면
        # "관리 필요이에요"가 된다. 따옴표로 값임을 드러내고 조사를 피한다.
        text = f"역병 위험 '{blight['risk']}' 등급이에요"
        if blight.get("management"):
            text += f" — {blight['management']}"
        return [_item(text, "역병자료")]
    if blight.get("documented"):
        return [_item("품종 자료에 역병이 관리 대상으로 적혀 있어요(위험 등급은 없어요)"
                      + (f" — {blight['management']}" if blight.get("management") else ""),
                      "역병자료")]
    # 미조사. 이건 '위험이 없다'가 아니라 '모른다'다 - 그대로 말한다.
    text = "역병 저항성 자료가 없어 품종별 강약을 말할 수 없어요"
    if blight.get("management"):
        text += f". 작물 공통 예방: {blight['management']}"
    return [_item(text, "역병자료(미조사)")]


def _confidence_cons(variety):
    """추정치를 확정된 사실처럼 보이지 않게 한다(breed.md §15)."""
    out = []
    bloom = variety.get("bloom_to_harvest") or {}
    conf = bloom.get("confidence")
    if conf and not str(conf).startswith(("확실", "보통")):
        out.append(_item(f"만개 후 {bloom.get('min')}~{bloom.get('max')}일로 알려져 있지만 "
                         f"추정치예요({conf})", "품종자료 신뢰도"))
    gd = variety.get("growth_days") or {}
    if gd.get("min") and not variety.get("growth_days_scorable"):
        out.append(_item(f"생육기간 {gd['min']}~{gd['max']}일은 추정치예요", "품종자료 신뢰도"))
    return out


def build(variety, region_pros=(), region_cons=(), blight=None, experience="beginner"):
    """품종 1건의 (pros, cons). region_* 는 엔진이 지역 기상으로 만든 근거다.

    지역 근거를 맨 앞에 둔다 - 사용자가 궁금한 건 "왜 **우리 동네에서** 이 품종인가"다.
    """
    pros, cons = [], []

    for t in region_pros:
        _add(pros, _item(t, "지역 기상") if isinstance(t, str) else t)
    for t in region_cons:
        _add(cons, _item(t, "지역 기상") if isinstance(t, str) else t)

    beginner_pro, beginner_con = _beginner(variety, experience)
    _add(pros, beginner_pro)

    for t in _texts(variety.get("selection_conditions"), 2):
        _add(pros, _item(f"{t}에 맞아요", "품종자료 선택조건"))

    _add(pros, _storage_pro(variety))
    _add(pros, _use_pro(variety))

    # ── cons ──
    _add(cons, beginner_con)

    # 이미 말한 병해 이름을 모아 둔다. key_warnings 에 같은 병이 또 적혀 있는 경우가
    # 많아서(수미: 병해충 '역병 위험 높음' + 주의사항 '역병에 약함') 그대로 넣으면
    # 같은 말이 두 줄 나간다. 카드에 4줄만 보이는데 그 중 2줄이 같으면 낭비다.
    mentioned = set()
    for it in _blight_items(blight):
        _add(cons, it)
        if blight and blight.get("assessed"):
            mentioned.add("역병")
    for it in _disease_cons(variety, skip_names=mentioned):
        _add(cons, it)
    for d in variety.get("diseases") or []:
        if d.get("level") == "높음" and d.get("name"):
            mentioned.add(d["name"])

    # key_warnings 는 데이터 제공자가 "이건 꼭 알아야 한다"고 적은 문장이다.
    for t in _texts(variety.get("key_warnings"), 4):
        if any(name in t for name in mentioned):
            continue
        _add(cons, _item(t, "품종자료 주의사항"))
    # 사과 risks / 오이 specific_risks
    raw = variety.get("_raw") or {}
    for field, label in (("risks", "품종자료 위험"), ("specific_risks", "품종자료 위험")):
        for t in _texts(raw.get(field), 3):
            _add(cons, _item(f"{t} 위험이 있어요" if len(t) < 12 else t, label))
    for it in _confidence_cons(variety):
        _add(cons, it)

    return pros[:MAX_PROS], cons[:MAX_CONS]
