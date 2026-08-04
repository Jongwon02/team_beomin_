# breed.md — 품종 추천 · 품종 리포트 구현 명세 (설계 단계)

| 항목 | 내용 |
|---|---|
| 문서 버전 | v0.2 (감자 4품종 구현 완료 — §16 구현 결과 참조) |
| 작성일 | 2026-08-04 |
| 상태 | **감자(추백·자영·수미·대서) 구현·검증 완료.** 나머지 4작물은 데이터 미확보 |
| 선행 문서 | `PRD.md`(§7 점수 로직) · `chatbot.md`(도구·프롬프트 규약) · `DB.md`(§4.6 기준 데이터) · `For_Frontend.md`(§3 점수 API 계약) |
| 표준 양식 실물 | `data/cultivar_reports/감자_추백-자영.md` (1호) |

---

## 0. 한 줄 요약

> 사용자가 고른 **작물 + 지역**에 대해, 그 지역의 **실측 토양·기후**와 **품종별 환경요구**를 맞춰
> **최적 품종을 순위로 추천**하고, 그 근거 데이터를 **LLM에 넘겨 "이 품종을 이 땅에서 어떻게 키우고
> 무엇을 조심할지"를 초보자 언어로 안내**한다.

**만든다**: 품종 기준 데이터(환경요구·특성) · 품종 적합도 점수 · 품종 리포트 양식 · 챗봇 도구 2개.
**안 만든다**: 수량/소득 예측, 기능성 성분 함량 예측, 종자 판매·유통 중개, 품종 신규 육성 정보 DB 전수화.

---

## 1. 왜 품종 레이어가 필요한가

지금 서비스는 **작물 5종 단위**에서 멈춘다. "충주 주덕읍 감자 84점"까지 나오지만, 사용자가 실제로
사는 것은 **씨감자 한 품종**이고 실패도 품종 선택에서 갈린다.

품종 차이는 **연 평년값에서 드러나지 않는다.** 같은 감자라도

- **추백**은 80~90일 만에 캐는 극조생종이라 **장마·고온 이전에 끝내는 작기**가 관건이고,
- **자영**은 110일 이상 걸리는 만생종이라 **무상기간 확보와 생육 후반 저온**이 관건이다.

즉 연평균기온·연강수량으로는 두 품종이 구분되지 않는다. 갈리는 지점은 **특정 작기 구간의
기온·서리·강수**다. 그래서 레이어를 이렇게 나눈다.

| 레이어 | 채점 단위 | 입력 | 이미 있는가 |
|---|---|---|---|
| 작물 적합도 (기존) | 지역 × 작물 | 연/생육기 평년값, 토양 화학성 | ✅ `backend/services/live_scoring.py` |
| **품종 적합도 (신규)** | 지역 × 작물 × **품종 × 작형(파종~수확 창)** | **작기 구간** 기온·서리·강수 + 품종 환경요구 | ❌ 이 문서 |

> 설계 원칙: **작물 점수는 건드리지 않는다.** 품종 점수는 그 위에 얹히는 별도 축이다.
> 사용자에게도 "감자는 이 지역에서 84점 / 그중 추백이 91점, 자영이 62점" 순서로 보여준다.

---

## 2. 산출물 3층 구조 — 누가 쓰고 무엇을 검수하는가

품종 정보는 **사람이 쓴 지식 원문**과 **기계가 채점하는 수치**를 섞으면 반드시 어긋난다.
(비교표에는 "80~90일", 본문에는 "약 3개월" 식으로 갈라진다.) 그래서 3층으로 분리한다.

| 층 | 형태 | 만드는 주체 | 쓰이는 곳 | 규칙 |
|---|---|---|---|---|
| **L1. 지식 원문** | Markdown 리포트 (`data/cultivar_reports/*.md`) | **사람**(농진청·농사로·논문 근거로 작성, 출처 필수) | 화면의 상세 리포트, LLM 근거 | LLM이 생성/수정 금지. 수치는 L2와 **한 글자도 달라선 안 됨** |
| **L2. 구조화 필드** | `data/cultivar_standards.json` → (온라인화 시) `public.cultivars` / `cultivar_env` | **사람**(L1에서 추출) | 점수 계산, 도구 응답, 화면 비교표 | 모든 수치의 **단일 진실 원본(SSOT)**. L1의 비교표는 L2에서 렌더 |
| **L3. 지역맞춤 문단** | LLM 생성 텍스트 (저장 시 캐시) | **LLM** (L1 섹션 + L2 + 지역 데이터 주입) | "이 땅에서는 이렇게" 문단, 챗봇 답변 | **새 수치 생성 금지** — 인용만. 생성 문단마다 근거 배지 표시 |

L1↔L2 불일치를 사람의 주의력에 맡기지 않는다 — **검증 스크립트**로 막는다(§12 B1).

```
data/scripts/check_cultivar_consistency.py
  · L2의 days_to_harvest / soil_ph / high_temp_stop 값이 L1 본문 문자열에 실제로 등장하는지 검사
  · L1에 있는 수치 패턴(\d+~\d+일, pH \d\.\d~\d\.\d)이 L2에 없으면 경고
  · CI 없이도 수동 실행 가능해야 한다(python data/scripts/check_cultivar_consistency.py)
```

---

## 3. 제공 정보 양식 — 표준 리포트 템플릿

1호 실물은 `data/cultivar_reports/감자_추백-자영.md`(추백·자영 감자)다. **모든 품종 리포트는
아래 골격을 따른다.** 섹션 번호·제목은 고정, 품종 수에 따라 §3·§4 블록만 반복된다.

### 3.1 섹션 골격 (고정)

| § | 제목 | 필수 요소 | 데이터 슬롯(L2에서 채움) |
|---|---|---|---|
| 1 | 리포트 개요 → 1.1 작성 목적 | 이 리포트를 읽고 무엇을 할 수 있게 되는지 1문장 + 품종별 재배 목적 대비 | `use_type`, `market_fit` |
| 2 | 품종별 핵심 비교 | **9행 고정 비교표**(§3.2) + 표를 풀어 쓴 2~3문장 | 표 전체가 L2 렌더 |
| 3·4… | 품종별 상세 (품종 1개당 1개 §) | ① 품종 특징 ② 적합한 농가 ③ 적합한 재배환경(온도/토양) ④ 재배 방법(단계 1~4) ⑤ 생육 관리(물주기·북주기 등) ⑥ 주의할 점 | `maturity_class`, `days_to_harvest`, `growing_temp`, `bulking_temp`, `soil_ph`, `drainage`, `disease_susceptibility`, `storability` |
| 5 | 두 품종의 공통 재배 일정 | 생육 단계별(파종 전 → 파종 → 출현 → 줄기생장 → 비대 → 성숙) 체크 항목 | `backend/chat_schedule.py`의 `CROP_SCHEDULE` + 품종 오프셋(§10.3) |
| 6 | 수확 방법 | 시험 수확 판단 기준 3~5개 + 상처·부패 예방 | `days_to_harvest`, `harvest_cues` |
| 7 | 저장 및 판매 | 품종별로 분리 서술 | `storability` |
| 8 | 초보 농업인이 가장 많이 하는 실수 | 5~6개, 각 "무엇을/왜 틀렸는지" | — |
| 9 | 품종 선택 결론 | 품종별 "이럴 때 고르세요" 체크 + **초보자 권장 1문장** | `difficulty` |
| 10 | 최종 체크리스트 | 파종 전 자문 10문항 + "이것들이 안 됐으면 면적을 줄이라"는 마무리 | — |
| — | 참고한 주요 연구·기술자료 | 저자·제목·발행처·연도 | `sources[]` |
| — | 면책 문구(고정) | "※ 정확한 파종일과 비료량은 지역·고도·토양·재배 작형에 따라 달라진다. 실제 재배 전에는 토양검정을 실시하고, 관할 농업기술센터의 지역별 재배기준을 함께 적용해야 한다." | — |

### 3.2 §2 비교표의 9개 고정 행

L2 필드와 1:1로 대응한다. **행을 늘리거나 순서를 바꾸지 않는다**(화면 비교표가 같은 순서를 쓴다).

| 비교표 행 | L2 필드 | 예시(추백 / 자영) |
|---|---|---|
| 주요 특징 | `traits.headline` | 수분이 많고 점성이 강한 햇감자 / 껍질과 속이 짙은 자주색인 기능성 감자 |
| 숙기 | `identity.maturity_class` | 극조생종 / 만생종 |
| 대략적인 생육기간 | `env.days_to_harvest` | 약 80~90일 / 110일 이상 |
| 주요 재배 목적 | `traits.use_type` | 봄철 조기 출하 / 기능성·컬러 감자 판매 |
| 유리한 환경 | `env.favorable_summary` | 봄철 평난지 조기재배 / 생육 후반이 서늘한 지역·작기 |
| 식감 | `traits.texture` | 수분감 많고 쫀득한 점질형 / 단단하고 색이 뚜렷 |
| 저장성 | `traits.storability.level`+`note` | 낮은 편 / 추백보다 휴면이 길지만 장기저장 전 품질 점검 필요 |
| 초보자 난이도 | `traits.difficulty` | 비교적 쉬움 / 중간 이상 |
| 가장 중요한 관리 | `traits.key_management` | 적기 수확과 바이러스 예방 / 충분한 재배기간과 후기 저온 확보 |

### 3.3 문체 규칙 (초보자 대상 · 기존 챗봇 규칙과 동일 계열)

1. 전문용어는 첫 등장에서 풀어 쓴다 — "땅속 감자인 괴경", "흙을 끌어올려주는 북주기".
2. 수치는 **L2에 있는 값만** 쓴다. "약", "이상", "내외"를 임의로 붙이거나 떼지 않는다.
3. **단정하지 않는 것**: 성분 함량·질병 효능, 수량·소득, 병해충 진단, 농약 상품명·희석배수.
   → "등록 약제와 농약안전사용기준 확인", "가까운 농업기술센터에 문의"로 넘긴다.
4. 실패 원인은 **점검 질문 형태**로 제시한다(§4.4 "색이 연하게 나오는 문제"가 표준 예).
5. 한 섹션은 화면 한 스크롤(약 400~800자) 안에 끝낸다.

---

## 4. 품종 데이터 스키마 (L2)

### 4.1 파일 레이아웃

```
crop_standards_v2.json            # (기존) 작물 5종 표준 — 손대지 않는다
data/cultivars/감자.json           # (구현) 품종 기준 데이터 = L2 정본. 작물별 1파일
data/cultivar_reports/
  감자_추백-자영.md                # (구현) L1 리포트 1호
  <작물>_<품종들>.md               # 이후 추가 (파일명이 곧 '이 리포트가 다루는 품종' 선언)
data/scripts/check_cultivar_consistency.py   # (구현) L1↔L2 정합성 검사
backend/scoring/cultivar_data.py   # (구현) L2 로더·정규화 (작물표준 폴백, 플래그 유도)
```

> ⚠️ **v0.1의 `data/cultivar_standards.json`(작물 통합 1파일 + 우리 스키마)은 만들지 않았다.**
> 실제로 들어온 데이터가 제공자 스키마(`dataset`/`common_management`/`varieties[]`)였고,
> 그 원본을 우리 스키마로 손번역해 넣으면 데이터가 갱신될 때마다 번역이 되풀이되면서
> 수치가 어긋난다. **원본을 그대로 두고 `cultivar_data.py`가 읽는 시점에 정규화**한다.
> 필드 대응은 §16.2 표에 있다.

### 4.2 핵심 규칙 — 품종 파일은 "작물 표준의 오버라이드"다

품종마다 pH·생육온도를 다시 적으면 `crop_standards_v2.json`과 반드시 어긋난다. 그래서:

> **품종 파일에는 작물 표준과 다른 값, 또는 작물 표준에 없는 품종 고유 항목만 적는다.**
> 조회 시 `crop_standards_v2.json[작물]`을 깔고 그 위에 품종 값을 덮는다(shallow merge, 필드 단위).

감자 pH 5.0~6.0은 작물 표준에 이미 있으므로 추백·자영 파일에는 **적지 않는다.**
반대로 `days_to_harvest`, `disease_susceptibility.PVY`, `late_season_cool`은 품종 고유라 여기 적는다.

### 4.3 필드 정의

| 그룹 | 필드 | 타입 | 채점 사용 | 비고 |
|---|---|---|---|---|
| identity | `crop` | enum(사과·배·오이·감자·상추) | — | 기존 5종 밖은 받지 않는다 |
| | `name` / `aliases[]` | text | — | 표기 통일용(자영 = 자영감자) |
| | `maturity_class` | enum(극조생·조생·중생·중만생·만생) | 간접 | 비교표 행 |
| | `registered_by` / `registered_year` | text / int | — | 예: 농촌진흥청 국립식량과학원 / 2009(자영) |
| | `seed_source` | text | — | 보급종·무병 씨감자 수급 경로 안내 문구 |
| traits | `headline` `texture` `key_management` `difficulty` | text/enum | 난이도만 필터 | §3.2 |
| | `use_type[]` `market_fit[]` | text[] | — | 판매 전략 문단 |
| | `storability` | `{level, dormancy_note, evidence}` | — | level: 낮음·보통·높음 |
| | `harvest_cues[]` | text[] | — | §6 시험수확 판단 기준 |
| **env** | `days_to_harvest` | `{min, max, unit:"일"}` | ✅ **하드 게이트** | 추백 80~90 / 자영 110~null(=이상) |
| | `growing_temp` | `{min, max, unit:"℃"}` | ✅ | 없으면 작물 표준 폴백 |
| | `bulking_temp` | `{min, max}` | ✅ | 감자=괴경 비대, 과수=결실·착색 적온 |
| | `high_temp_stop` | `{threshold, note}` | ✅ | 감자 25(경향)/27~30(정지) |
| | `late_season_cool` | `{preferred: bool, why}` | ✅ (해당 품종만) | 자영: true(안토시아닌 축적) |
| | `soil_ph` | `{min, max}` | ✅ | 대개 작물 표준 폴백 |
| | `drainage` | `{requirement, flood_note}` | ✅ | requirement: 보통·양호·필수 |
| | `soil_texture[]` | text[] | 보조 | 모래참흙·참흙 |
| | `cultivation_types[]` | `[{code, name, fit}]` | ✅ | code는 `crop_standards_v2.json`의 `crop_codes` 재사용(감자: 03001 남부_가을재배 / 03002 준고랭지_고랭지 / 03003 남부_봄재배) |
| | `altitude_pref` | `{min, max, note}` | 보조 | 자영: 고도 높을수록 안토시아닌 유리 |
| risks | `disease_susceptibility` | `{병명: 낮음·보통·높음}` | ✅ | 추백 PVY=높음 |
| | `frost_sensitivity` / `waterlogging_sensitivity` | enum | ✅ | 감자 침수: 수확기 24시간이면 부패 시작 |
| meta | `sources[]` | `[{claim, ref}]` | — | **수치마다 근거 1개 이상** |
| | `confidence` | `{필드명: 높음·보통·낮음}` | 신뢰도 표기 | `crop_standards_v2.json`의 `confidence` 관례 승계 |
| | `report` | text(파일 경로) | — | L1 링크 |

### 4.4 JSON 예시 (감자 추백·자영 — 1호 실물과 동일 값)

```json
{
  "_meta": {
    "title": "품종 기준 데이터 (작물 표준 crop_standards_v2.json의 오버라이드)",
    "merge_rule": "crop_standards_v2.json[crop] 위에 필드 단위로 덮어쓴다. 없는 필드는 작물 표준을 그대로 쓴다.",
    "notes": ["수치는 반드시 sources[]의 근거와 짝지어 적는다", "L1 리포트 본문과 수치가 다르면 검증 스크립트가 잡는다"]
  },
  "감자": {
    "추백": {
      "identity": {
        "maturity_class": "극조생종",
        "seed_source": "바이러스 검사를 거친 보급종 또는 무병 씨감자"
      },
      "traits": {
        "headline": "수분이 많고 점성이 강한 햇감자",
        "use_type": ["봄철 조기 출하", "식용(감자전·볶음·조림)"],
        "texture": "수분감이 많고 쫀득한 점질형",
        "difficulty": "비교적 쉬움",
        "key_management": "적기 수확과 바이러스 예방",
        "storability": {
          "level": "낮음",
          "dormancy_note": "휴면기간이 짧고 수분이 많아 장기저장에 불리",
          "evidence": "실온 저장 1개월 후 발아율 약 99%, 2개월 시 중량 감소 큼"
        },
        "market_fit": ["수확 직후 판매", "품종별 식감 강조 판매"],
        "harvest_cues": ["목표 상품 크기 도달", "문질렀을 때 껍질이 쉽게 벗겨지지 않음", "썩음·갈라짐·벌레 피해 없음"]
      },
      "env": {
        "days_to_harvest": { "min": 80, "max": 90, "unit": "일",
          "note": "시험에서 상품수량이 약 90일 재배에서 최대 수준, 100일 연장 시 추가 이점 제한적" },
        "bulking_temp": { "min": 14, "max": 18, "unit": "℃" },
        "high_temp_stop": { "threshold": 25, "unit": "℃", "note": "27~30℃에서 비대 정지" },
        "late_season_cool": { "preferred": false, "why": "조기 수확형이라 후기 저온보다 장마·고온 회피가 중요" },
        "favorable_summary": "봄철 평난지 조기재배",
        "drainage": { "requirement": "양호", "flood_note": "수확기 24시간 침수 시 부패 시작, 침수시간 길어질수록 부패율 급증" },
        "soil_texture": ["모래참흙", "참흙"],
        "cultivation_types": [
          { "code": "03003", "name": "남부_봄재배", "fit": "적합" },
          { "code": "03001", "name": "남부_가을재배", "fit": "보통" },
          { "code": "03002", "name": "준고랭지_고랭지", "fit": "보통" }
        ]
      },
      "risks": {
        "disease_susceptibility": { "감자바이러스Y(PVY)": "높음", "역병": "보통", "무름병": "보통" },
        "frost_sensitivity": "보통",
        "waterlogging_sensitivity": "높음"
      },
      "confidence": { "days_to_harvest": "보통", "storability": "보통", "disease_susceptibility": "보통" },
      "sources": [
        { "claim": "생육기간 80~90일 · 90일 상품수량 최대", "ref": "Won 외, 추백·대서·수미의 재배기간과 저장환경에 따른 품질 연구, 2024" },
        { "claim": "실온 1개월 발아율 약 99%", "ref": "동일" },
        { "claim": "PVY 감수성 높음", "ref": "농촌진흥청 농사로 감자 품종·병해충 자료" }
      ],
      "report": "data/cultivar_reports/감자_추백-자영.md"
    },

    "자영": {
      "identity": {
        "maturity_class": "만생종",
        "registered_by": "농촌진흥청 국립식량과학원",
        "registered_year": 2009,
        "seed_source": "무병 씨감자(품종 혼입 주의)"
      },
      "traits": {
        "headline": "껍질과 속이 짙은 자주색인 기능성 감자",
        "use_type": ["기능성·컬러 감자 판매"],
        "texture": "비교적 단단하고 색이 뚜렷한 편",
        "difficulty": "중간 이상",
        "key_management": "충분한 재배기간과 후기 저온 확보",
        "storability": { "level": "보통", "dormancy_note": "추백보다 휴면이 길지만 장기저장 전 품질 점검 필요", "evidence": null },
        "market_fit": ["직거래·로컬푸드·체험농장", "컬러·프리미엄 선별 판매"],
        "harvest_cues": ["속색과 괴경 크기가 충분히 발달", "껍질이 손으로 쉽게 벗겨지지 않음"]
      },
      "env": {
        "days_to_harvest": { "min": 110, "max": null, "unit": "일", "note": "110일 이상. 파종일을 예상 수확일에서 역산해 확인" },
        "bulking_temp": { "min": 14, "max": 18, "unit": "℃" },
        "high_temp_stop": { "threshold": 25, "unit": "℃", "note": "비대기 고온은 비대·안토시아닌 축적 모두에 불리" },
        "late_season_cool": { "preferred": true, "why": "수확 전 생육 후반 기온이 낮을수록 안토시아닌 축적에 유리" },
        "favorable_summary": "생육 후반이 서늘한 지역·작기",
        "altitude_pref": { "min": 300, "max": null, "note": "전국 14개 지역 시험에서 대체로 고도가 높은 지역에서 안토시아닌 함량이 높았음. 단 첫서리가 이른 지역은 생육기간 부족" },
        "drainage": { "requirement": "양호", "flood_note": "감자 공통 — 침수에 매우 약함" },
        "cultivation_types": [
          { "code": "03002", "name": "준고랭지_고랭지", "fit": "적합" },
          { "code": "03001", "name": "남부_가을재배", "fit": "적합" },
          { "code": "03003", "name": "남부_봄재배", "fit": "보통" }
        ]
      },
      "risks": {
        "disease_susceptibility": { "역병": "보통", "감자바이러스Y(PVY)": "보통" },
        "frost_sensitivity": "높음",
        "waterlogging_sensitivity": "높음"
      },
      "confidence": { "days_to_harvest": "높음", "altitude_pref": "보통", "late_season_cool": "보통" },
      "sources": [
        { "claim": "만생종·110일 이상·안토시아닌 품종", "ref": "박영은 외, 「Anthocyanin 함량이 높은 감자 신품종 '자영'」, 한국육종학회지, 2009" },
        { "claim": "고도·후기 기온과 안토시아닌 함량 관계", "ref": "정진철 외, 「컬러감자 안토시아닌 색소발현에 관여하는 재배환경 조건」" }
      ],
      "report": "data/cultivar_reports/감자_추백-자영.md"
    }
  }
}
```

---

## 5. 지역 지표 — 이미 있는 것 / 새로 계산할 것

### 5.1 이미 있는 것 (그대로 재사용)

| 지표 | 출처 | 호출 |
|---|---|---|
| 온도·강수·일조·pH·유기물·유효인산·EC 점수 | `backend/services/live_scoring.py` | `get_live_score(region_name, crop)` → `GET :8002/api/crop-score/<작물>?region=` |
| 냉해·폭염 위험 신호 | 같은 응답 `risk_signals` | — |
| 기후 클러스터(K=6) | `data/processed/region_cluster_map.json` (89개 관측소) | 응답의 `cluster_id`/`cluster_name` |
| 최근접 관측소·거리 | `region_mapper.find_nearest_station` | `matched_station`, `distance_km` |
| 앞으로 7일 예보 | `Beomin_web/news_server.py` → `backend/api/weekly_fcst.py` | `GET :8001/api/weekly/<지역>` |
| 토양 화학성(원시값) | `backend/api/soil.py` | `get_soil_readings(sigungu_full_name, crop)` |

클러스터 6종은 품종 추천의 **거친 사전 필터**로 쓴다(0 중산간내륙형 / 1 중남부저지대형 /
2 고랭지형 / 3 중부내륙형 / 4 남부해안형 / 5 도서형). 예: 자영은 2·0에서 가점, 5에서 감점.

### 5.2 새로 계산해야 하는 파생 지표 (신규 모듈)

```
backend/scoring/season_window.py   # 신규
```

| 지표 | 정의 | 계산 | 왜 필요한가 |
|---|---|---|---|
| `frost_free_days` | 무상기간 | ASOS 일자료 최근 10년에서 `minTa ≤ 0℃`인 날 중 **봄 마지막 서리일**과 **가을 첫 서리일** → 두 날짜 사이 일수. 대표값은 **10년 중 위험 쪽(=짧은 쪽) 20퍼센타일** | 자영(110일 이상)의 재배기간 확보 판정 = 하드 게이트 |
| `last_spring_frost` / `first_fall_frost` | 위 두 날짜(월-일) | 동일 | 파종 가능창 제시, 캘린더 보정 |
| `window_mean_temp` | 작기 구간 평균기온 | 파종창~수확 예상일 사이 `avgTa` 평균(10년 평년) | 생육 적온 대조 |
| `bulking_mean_temp` | 비대·결실기 평균기온 | 수확 전 30일(감자 비대기 근사) 평균 | 비대 적온 14~18℃ 대조 |
| `late_season_delta` | 후기 냉량성 | `bulking_mean_temp − window_mean_temp` (음수일수록 후기가 서늘) | 자영 등 `late_season_cool.preferred` 품종 채점 |
| `hot_days_in_window` | 작기 내 고온일수 | 작기 구간 `maxTa > high_temp_stop.threshold` 일수 | 비대 정지 위험 |
| `window_rain_mm` / `heavy_rain_days` | 작기 강수·집중도 | 구간 `sumRn` 합, `sumRn ≥ 50mm` 일수 | 침수·역병 위험 |
| `station_altitude` | 관측소 고도 | **이미 있다** — `data/raw/climate_clustering_final_v3.csv`의 `elevation` 컬럼(대관령 772m·태백 714m·정선군 312m·충주 115m). `station_id`로 조인 | 고랭지 작형 성립 판정 |

구현 메모
- ASOS 호출은 `backend/api/asos.py`의 `get_daily_records(station_id, start, end)`를 그대로 쓴다.
  10년 × 365일은 호출이 무거우므로 **관측소별 파생 지표만 미리 계산해 캐시**한다:
  `data/processed/station_season_metrics.json` (관측소 89개 × 지표 9개, 배치 스크립트로 갱신).
- 과거 실측은 불변이므로 캐시 만료 없음. 연 1회 갱신(1월)만 한다. (PRD §8 캐싱 원칙과 동일)
- 파생 지표가 결측인 관측소는 **해당 항목을 점수에서 제외하고 가중치를 재정규화**한다
  (기존 `reliability` / `excluded_variables` 규약을 그대로 승계).

---

## 6. 품종 적합도 산출 로직

### 6.1 채점 단위

```
(지역, 작물, 품종, 작형)  →  cultivar_fit  0~100  +  근거  +  파종 권장창
```

작형은 `cultivation_types[].code`를 순회한다. 한 품종이 여러 작형을 가지면 **작형별로 채점하고
가장 높은 작형을 대표로** 올린다(응답에는 전 작형 점수를 함께 담아 "가을재배로는 79점" 안내 가능).

### 6.2 항목과 가중치 (구현값 — v0.1에서 8항목으로 늘었다)

| 항목 | 가중치 | 입력 | 판정 |
|---|--:|---|---|
| 재배기간 확보 | 28 | `frost_free_days` vs 생육일수, **수확→첫서리 여유** | 두 축의 **나쁜 쪽**을 쓴다(§16.3) |
| 파종·출현기 | 12 | 파종 후 20일 평균기온 | 10~22℃ 만점 · 25℃↑ 씨감자 부패 · 5℃↓ 출현 지연 |
| 비대·결실기 온도 | 24 | `bulking_mean_temp` + 고온일수 | 적온 대조 후 고온일수 감점(같은 원인 이중감점 방지) |
| 토양 | 14 | 지역 pH/유기물/유효인산 | pH 0.6 : 유기물 0.2 : 유효인산 0.2 가중 평균 |
| 강수·과습 | 12 | 작기 강수·집중강수일수 | 부족(기존 기준값을 작기 길이로 환산)과 과습 중 나쁜 쪽 |
| 병해 위험 | 10 | 품종 감수성 × 지역 습윤도 | 바이러스병은 습윤 가중 없이 고정 감점 |
| 후기 저온 | 8 | `late_delta` | **색소·기능성 품종만**(자영). 해당 없으면 항목 제외 후 재정규화 |
| 출하시기 | 8 | 수확 예정일 | **조기출하 목적 품종만**(추백). 6월 상순까지 만점 |

> 조건부 2항목(후기저온·출하시기)은 **품종 데이터에 그 목적이 적혀 있을 때만** 붙는다.
> '해당 없으면 100점'으로 처리하지 않는다 — 목적이 안 적힌 품종이 공짜 만점을 받아
> 순위가 뒤집힌다. 난이도는 여전히 점수가 아니라 배지·동점 정렬로만 쓴다(§6.5).

`hot_days_in_window`는 별도 항목이 아니라 **비대·결실기 온도 항목 안의 감점 인자**로 넣는다
(같은 원인을 두 번 깎지 않기 위해).

### 6.3 점수 함수 — 기존 로직 재사용 + 하드 게이트

연속 항목은 `PRD.md §7.2`의 완만 감점 함수를 그대로 쓴다.

```
최적범위 안                → 100
최적에서 near 이내 이탈     → 100 → 80 (완만)
near 초과                  → 80부터 2배 속도로 → 0
  near: 온도 2℃ / 강수 150mm / 무상기간 15일
```

**하드 게이트** — 재배기간은 완만 감점으로 표현할 수 없다. 110일 품종을 95일 무상기간 땅에
심으면 "조금 불리"가 아니라 **실패**다. 그래서 종합 점수에 상한을 씌운다.

| 조건 | 처리 |
|---|---|
| `frost_free_days < days_to_harvest.min` | 종합 상한 **20** (등급 '위험') + `blockers: ["재배기간 부족"]` |
| `days_to_harvest.min ≤ frost_free_days < min + 10일` | 종합 상한 **40** (등급 '주의') + 경고 |
| 여유 10일 이상 | 상한 없음 |

동일 방식의 게이트를 하나 더 둔다: `waterlogging_sensitivity=높음`인데 지역 배수 등급이
불량이면 상한 **60** + `blockers: ["배수 개선 필요"]`. (개선 가능한 요인이므로 '위험'이 아니라
'상한 + 개선 팁' 형태 — PRD §F-4의 "감점 요인 → 개선 팁" 연결과 같은 취급.)

### 6.4 등급·신뢰도

`For_Frontend.md §3`의 매핑을 그대로 쓴다(80↑ good/우수, 60~79 normal/양호, 40~59 caution/주의,
40↓ bad/위험). 신뢰도도 기존 `reliability`(정상·주의·신뢰불가) + `excluded_variables` 규약 승계.
품종 데이터의 `confidence`가 '낮음'인 필드가 채점에 쓰였으면 `reliability_reason`에 덧붙인다.

### 6.5 난이도는 점수가 아니다

`difficulty`(초보자 난이도)를 점수에 섞으면 "환경은 최적인데 점수가 낮은" 혼란이 생긴다.
난이도는 **별도 배지 + 동점 시 타이브레이커**로만 쓴다.

```
동점(±2점) 시 정렬: difficulty(쉬움 → 중간 → 어려움) → days_to_harvest 짧은 순
사용자 경험 수준(experience=beginner)이면 '어려움' 품종에 "초보자에겐 소규모 시험재배 권장" 배지
```

### 6.6 워크된 예시 (설계 검증용 · 구현 후 실측으로 대체)

| 지역 | 클러스터 | 무상기간(가정) | 추백 | 자영 | 해석 |
|---|---|--:|--:|--:|---|
| 충청북도 충주시 주덕읍 | 1 중남부저지대형 | 195일 | **91 / 우수** | 68 / 양호 | 봄 조기재배 창이 넓어 추백 유리. 자영은 비대 후기가 한여름에 걸려 감점(후기 저온 항목 42점) |
| 강원특별자치도 평창군 | 2 고랭지형 | 128일 | 84 / 우수 | **88 / 우수** | 무상기간 128일 > 110+10 → 게이트 통과. 후기 서늘로 자영 가점 |
| 강원특별자치도 태백시 | 2 고랭지형 | 112일 | 82 / 우수 | **40 상한 / 주의** | 여유 2일 → 게이트 발동. "첫서리가 이르면 생육기간 부족" 경고 |
| 제주특별자치도 | 5 도서형 | 300일+ | 76 / 양호 | 55 / 주의 | 월동재배 창은 넓지만 일조 최저·후기 온화로 자영 색 발현 불리 |

> 이 표의 숫자는 **설계 의도를 고정하기 위한 가상값**이다. 구현 후 실측으로 교체하고,
> 교체 전에는 화면·문서 어디에도 노출하지 않는다.

### 6.7 하지 않는 것

- 수량(kg/10a)·소득 예측 — 데이터 없음.
- 안토시아닌 함량 등 **성분 수치 예측** — "고도·후기 기온이 유리/불리한 방향"까지만 말한다.
- 품종 간 우열 단정 — 항상 "무엇을 하려는 농가에 유리한가"로 서술한다.

---

## 7. API 계약

기존 서버 배치를 따른다. **품종은 점수 엔진 쪽 관심사**이므로 `crop_score_server.py`(:8002)에
붙인다. 프런트/챗봇은 :8001을 거치지 않고 :8002를 직접 부른다(현재 챗봇 도구도 :8002를 부른다).

> 아래는 설계 시점의 계약이다. **실제 구현된 응답은 §16을 함께 보라** — 항목이 8개로 늘었고
> (`파종출현`·`출하시기` 추가), `cultivation_type`은 객체가 아니라 작형 이름 문자열이며,
> `blockers`/`cautions`/`variety_warnings`가 분리되어 있다. 프런트·챗봇은 구현된 형태를 쓴다.

### 7.1 `GET :8002/api/cultivars/<작물>` — 품종 목록·특성(지역 무관)

```json
{
  "crop": "감자",
  "count": 2,
  "cultivars": [
    { "name": "추백", "maturity_class": "극조생종", "days_to_harvest": "80~90일",
      "use_type": ["봄철 조기 출하"], "difficulty": "비교적 쉬움",
      "headline": "수분이 많고 점성이 강한 햇감자", "has_report": true },
    { "name": "자영", "maturity_class": "만생종", "days_to_harvest": "110일 이상",
      "use_type": ["기능성·컬러 감자 판매"], "difficulty": "중간 이상",
      "headline": "껍질과 속이 짙은 자주색인 기능성 감자", "has_report": true }
  ],
  "source": "data/cultivar_standards.json + crop_standards_v2.json"
}
```

### 7.2 `GET :8002/api/cultivar-score/<작물>?region=&experience=&cultivation_type=`

- `region` (필수): 시군구 또는 "도 시군구 읍면동" — 기존 `/api/crop-score`와 동일 규약.
- `experience` (선택): `beginner`|`experienced` (기본 `beginner`) — 배지·정렬에만 영향.
- `cultivation_type` (선택): 작형 코드. 생략 시 전 작형 채점 후 최고값 대표.

```json
{
  "status": "matched",
  "crop": "감자",
  "region": "강원특별자치도 평창군",
  "crop_score": { "score": 86.2, "grade_label": "우수" },
  "region_metrics": {
    "cluster_id": 2, "cluster_name": "고랭지형",
    "matched_station": "대관령", "distance_km": 8.4, "station_altitude": 772,
    "frost_free_days": 128, "last_spring_frost": "05-11", "first_fall_frost": "09-16",
    "window_mean_temp": 16.4, "bulking_mean_temp": 15.1, "late_season_delta": -1.3,
    "hot_days_in_window": 3, "window_rain_mm": 812.5, "heavy_rain_days": 4
  },
  "ranking": [
    {
      "cultivar": "자영", "cultivation_type": { "code": "03002", "name": "준고랭지_고랭지" },
      "score": 88.0, "grade": "good", "grade_label": "우수",
      "breakdown": {
        "재배기간": { "value": "무상 128일 / 필요 110일", "score": 82, "weight": 30 },
        "비대온도": { "value": 15.1, "score": 100, "weight": 25 },
        "토양":    { "value": "pH 5.6 · 배수 양호", "score": 92, "weight": 15 },
        "강수침수": { "value": "812mm · 집중강수 4일", "score": 74, "weight": 12 },
        "병해위험": { "value": "역병 보통 × 강수 많음", "score": 70, "weight": 10 },
        "후기저온": { "value": -1.3, "score": 95, "weight": 8 }
      },
      "blockers": [],
      "cautions": ["첫서리 9월 16일 — 파종이 6월을 넘기면 생육기간이 부족해질 수 있어요"],
      "planting_window": { "from": "05-21", "to": "06-05",
        "why": "마지막 서리(05-11) 이후 + 수확까지 110일 확보" },
      "badges": ["초보자에겐 소규모 시험재배 권장"],
      "reasons": [
        "무상기간 128일로 자영의 최소 110일을 여유 18일로 넘겨요",
        "비대기 평균 15.1℃가 적온(14~18℃) 안에 들어요",
        "생육 후반이 1.3℃ 더 서늘해 색이 진해지는 방향이에요"
      ]
    },
    {
      "cultivar": "추백", "cultivation_type": { "code": "03003", "name": "남부_봄재배" },
      "score": 84.0, "grade": "good", "grade_label": "우수",
      "breakdown": { "…": {} },
      "blockers": [], "cautions": ["저장성이 낮아 수확 직후 판매 계획이 필요해요"],
      "planting_window": { "from": "05-11", "to": "05-25", "why": "…" },
      "badges": [], "reasons": ["…"]
    }
  ],
  "reliability": "주의",
  "reliability_reason": "제외된 항목: EC(결측) · 품종 데이터 신뢰도 낮음: 없음",
  "excluded_variables": ["EC"],
  "data_sources": {
    "기상": "기상청 ASOS 일자료 10년 평년(관측소 대관령)",
    "토양": "흙토람 SoilExamStat V2",
    "품종": "data/cultivar_standards.json (근거: 박영은 외 2009 등)"
  }
}
```

### 7.3 `GET :8002/api/cultivar-report/<작물>/<품종>?section=&region=`

- `section` 생략 시 **목차 + 각 섹션 첫 문단만**(응답 폭주 방지). 전문은 `section=all`.
- `section=2` 처럼 번호 지정 시 해당 섹션 Markdown + 그 섹션이 쓴 L2 필드 목록.
- `region`이 오면 L3(지역맞춤 문단)을 함께 채워 준다(§8.5).

### 7.4 실패 규약 (기존과 동일)

```json
{ "error": "지원하지 않는 작물명입니다: '딸기'" }
{ "error": "품종 데이터가 아직 없어요: 감자 '두백'" }
{ "error": "지역을 찾지 못했어요: 없는동 (unmatched)" }
```

`status != "matched"`면 점수 필드를 만들어 넣지 않는다(0점으로 위장 금지).

---

## 8. LLM 연동 — `chatbot.md` 확장

기존 도구 3개(`get_crop_score`·`get_weather`·`get_crop_schedule`) 옆에 **2개를 더한다.**
`chatbot.md §6`의 대원칙(**서버에서 반드시 축약**)을 그대로 적용한다 — §7.2 응답 원본은
`region_metrics`·`breakdown`까지 합쳐 1,000토큰을 넘는다.

### 8.1 `get_cultivar_candidates`

```json
{
  "name": "get_cultivar_candidates",
  "description": "특정 지역·작물에서 어떤 품종이 잘 맞는지 실측 기상·토양과 품종 환경요구로 비교해 순위로 돌려줍니다. '어떤 품종 심어요', '추백이랑 자영 중에 뭐가 나아요', '우리 동네에 맞는 감자 품종' 같은 질문에 호출하세요.",
  "input_schema": {
    "type": "object",
    "properties": {
      "crop":   { "type": "string", "enum": ["사과","배","오이","감자","상추"] },
      "region": { "type": "string", "description": "시군구 또는 '도 시군구 읍면동'" },
      "experience": { "type": "string", "enum": ["beginner","experienced"],
                      "description": "생략하면 beginner" }
    },
    "required": ["crop", "region"],
    "additionalProperties": false
  },
  "strict": true
}
```

**축약 규칙** — 상위 **3개 품종**, 품종당 아래 형태(전체 350토큰 이내):

```python
{
  "작물": d["crop"], "지역": d["region"],
  "작물점수": d["crop_score"]["score"],
  "지역요약": {                                  # region_metrics에서 4개만
      "기후대": d["region_metrics"]["cluster_name"],
      "무상기간일": d["region_metrics"]["frost_free_days"],
      "첫서리": d["region_metrics"]["first_fall_frost"],
      "비대기평균기온": d["region_metrics"]["bulking_mean_temp"],
  },
  "품종": [{
      "이름": r["cultivar"], "점수": round(r["score"]), "등급": r["grade_label"],
      "작형": r["cultivation_type"]["name"],
      "이유": r["reasons"][:2],                  # 2개까지
      "막는요인": r["blockers"],                 # 있으면 반드시 말하게
      "주의": r["cautions"][:1],
      "파종권장창": f'{r["planting_window"]["from"]}~{r["planting_window"]["to"]}',
  } for r in d["ranking"][:3]],
  "신뢰도": d["reliability"], "신뢰도_사유": d.get("reliability_reason"),
}
```

- `breakdown`·`data_sources`·4위 이하는 **넣지 않는다.**
- `blockers`가 비어 있지 않으면 축약에서 절대 빼지 않는다 — 이걸 빠뜨리면 모델이
  "심어도 괜찮다"고 답한다.

### 8.2 `get_cultivar_profile`

```json
{
  "name": "get_cultivar_profile",
  "description": "한 품종의 특징·재배환경·재배방법·주의점·저장판매 정보입니다. '자영은 어떤 감자예요', '추백 저장 잘 돼요', '자영 심을 때 조심할 점' 같은 질문에 씁니다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "crop":     { "type": "string", "enum": ["사과","배","오이","감자","상추"] },
      "cultivar": { "type": "string", "description": "품종명. 예: 추백, 자영" },
      "topic":    { "type": "string",
                    "enum": ["개요","재배환경","재배방법","생육관리","주의점","수확","저장판매"],
                    "description": "생략하면 개요" }
    },
    "required": ["crop", "cultivar"],
    "additionalProperties": false
  },
  "strict": true
}
```

**축약 규칙** — 리포트 전문을 넣지 않는다. 요청한 `topic` 한 섹션(최대 500자) + L2 핵심 6필드
(`maturity_class`, `days_to_harvest`, `difficulty`, `key_management`, `storability.level`,
`disease_susceptibility` 중 '높음'인 것) + `sources[]`의 `ref` 1~2개.
데이터가 없는 품종은 `{"조회실패": "그 품종은 아직 자료가 없어요", "있는품종": ["추백","자영"]}`.

### 8.3 시스템 프롬프트 추가 블록

`chatbot.md §4`의 고정 프롬프트(캐시 대상)에 아래를 **덧붙인다**. 지역·날짜는 절대 넣지 않는다.

```
# 품종 안내 규칙
- 품종 비교·추천은 get_cultivar_candidates, 한 품종 설명은 get_cultivar_profile로만
  답합니다. 도구에 없는 품종은 "안농에는 아직 그 품종 자료가 없어요"라고 말합니다.
- 점수를 말할 때는 그 품종이 왜 유리/불리한지 근거 항목을 1~2개 함께 말합니다.
  예: "무상기간이 128일이라 110일 걸리는 자영도 가능해요."
- '막는요인'(blockers)이 있으면 점수보다 먼저 말합니다. 재배기간이 부족한 품종을
  "괜찮다"고 답하지 않습니다.
- 성분 함량(안토시아닌 등)이나 건강 효능을 수치로 말하지 않습니다. "재배환경에 따라
  달라진다"까지만 말하고, 판매 문구로 쓰려면 성분검사가 필요하다고 안내합니다.
- 파종일과 비료량은 도구가 준 권장창을 '기준'으로만 제시하고, 토양검정과 관할
  농업기술센터의 지역 재배기준을 함께 확인하라고 덧붙입니다.
- 씨감자·묘목은 상호나 판매처를 추천하지 않습니다. "검정을 거친 보급종·무병 씨감자"를
  쓰라는 원칙만 안내합니다.
```

### 8.4 화면 맥락 추가 (`render_context`)

사용자가 이미 품종을 골라 저장했으면 되묻지 않는다. `chatbot.md §5` 블록에 한 줄을 더한다.

```
선택 품종: 감자 '자영' (파종 예정 2027-05-25)
```

값이 없으면 줄 자체를 빼는 기존 규칙 그대로.

### 8.5 L3 지역맞춤 문단 생성 (배치 · 대화와 분리)

리포트 화면의 "이 땅에서는 이렇게" 문단은 **대화 루프가 아니라 별도 호출**로 만든다
(스트리밍 불필요, 캐시 가능, 실패해도 리포트는 보여야 함).

```
입력: L1 해당 섹션 원문 + L2 품종 필드 + §7.2 응답(region_metrics·breakdown·blockers)
출력: 3~5문장 × 섹션당 1문단. 새 수치 생성 금지(주입된 값만 인용)
캐시 키: (crop, cultivar, region, section, L1 파일 해시, L2 버전)  · TTL 30일
실패 시: 문단을 비우고 L1 원문만 보여준다(빈 문단에 "지금은 지역 맞춤 설명을 못 만들었어요")
```

프롬프트 제약(고정): "주입된 수치만 인용한다 · 없는 값은 언급하지 않는다 · 효능·수량을
단정하지 않는다 · 3~5문장." 생성 문단에는 화면에서 **`AI 생성` 배지 + 근거 데이터 툴팁**을 붙인다.

### 8.6 비용·가드레일

- 도구 응답 축약 상한: `get_cultivar_candidates` 350토큰 / `get_cultivar_profile` 400토큰.
  (`chatbot.md §9`의 턴당 비용 추정에 품종 도구 1회 호출 = 약 +400토큰 입력으로 반영)
- L3 배치 생성은 **캐시 히트 우선**. 같은 (품종·지역·섹션) 재생성 금지.
- 시스템 프롬프트가 2,000토큰을 넘으면 `# 품종 안내 규칙`을 6줄로 압축한다
  (`chatbot.md §4` 각주의 토큰 상한 규칙 승계).
- `MAX_TOOL_ROUNDS = 4` 그대로 — 품종 도구가 늘어도 라운드는 늘리지 않는다.

---

## 9. DB 스키마 (Supabase) — `DB.md §4.6` 확장

파일(L1·L2)로 먼저 구현하고, 온라인 전환 시 아래 테이블로 이관한다. **기준 데이터 관례
(누구나 읽기 / 쓰기는 service_role만)를 그대로 따른다.**

```sql
create table public.cultivars (                 -- data/cultivar_standards.json (identity·traits)
  crop            text not null,
  name            text not null,
  aliases         text[] not null default '{}',
  maturity_class  text,
  registered_by   text,
  registered_year int,
  traits          jsonb not null default '{}',  -- headline·texture·difficulty·storability·market_fit
  report_path     text,                         -- data/cultivar_reports/*.md (또는 storage 키)
  primary key (crop, name)
);

create table public.cultivar_env (              -- 채점에 쓰는 환경요구. crop_standards를 덮어쓴다
  crop        text not null,
  name        text not null,
  metric      text not null,                    -- days_to_harvest / bulking_temp / soil_ph / …
  min_value   double precision,
  max_value   double precision,
  unit        text,
  extra       jsonb,                            -- note·preferred·requirement 등 비수치 속성
  confidence  text,                             -- 높음 / 보통 / 낮음
  source      text not null,
  primary key (crop, name, metric),
  foreign key (crop, name) references public.cultivars (crop, name) on delete cascade
);

create table public.cultivar_reports (          -- L1 원문(섹션 단위로 쪼개 저장)
  crop        text not null,
  name        text not null,
  section     text not null,                    -- '1' '2' '3.2' …
  title       text not null,
  body_md     text not null,
  reviewed_at date,                             -- 사람 검수일. 없으면 화면에 '검수 전' 표기
  primary key (crop, name, section),
  foreign key (crop, name) references public.cultivars (crop, name) on delete cascade
);

create table public.cultivar_text_cache (       -- L3 지역맞춤 문단 캐시 (서버 전용)
  cache_key   text primary key,                 -- crop|name|region|section|l1hash|l2ver
  body        text not null,
  model       text not null,
  created_at  timestamptz not null default now()
);

create table public.station_season_metrics (    -- §5.2 파생 지표
  station_id        text primary key,
  frost_free_days   int,
  last_spring_frost text,                       -- 'MM-DD'
  first_fall_frost  text,
  altitude_m        double precision,
  metrics           jsonb,                      -- window_mean_temp 등 작형별 값
  computed_at       date not null
);

-- 사용자 선택: my_farm에 품종 컬럼 추가 (DB.md §4.3 my_farm 확장)
alter table public.my_farm add column cultivar text;              -- 품종명 (없으면 미선택)
alter table public.my_farm add column cultivation_type text;      -- 작형 코드
alter table public.my_farm add column planting_date date;         -- 파종/정식 예정일
```

RLS

```sql
alter table public.cultivars              enable row level security;
alter table public.cultivar_env           enable row level security;
alter table public.cultivar_reports       enable row level security;
alter table public.station_season_metrics enable row level security;
alter table public.cultivar_text_cache    enable row level security;  -- 정책 없음 = service_role 전용

create policy p_cultivars_read              on public.cultivars              for select using (true);
create policy p_cultivar_env_read           on public.cultivar_env           for select using (true);
create policy p_cultivar_reports_read       on public.cultivar_reports       for select using (true);
create policy p_station_season_metrics_read on public.station_season_metrics for select using (true);

revoke insert, update, delete on public.cultivars              from anon, authenticated;
revoke insert, update, delete on public.cultivar_env           from anon, authenticated;
revoke insert, update, delete on public.cultivar_reports       from anon, authenticated;
revoke insert, update, delete on public.station_season_metrics from anon, authenticated;
```

`cultivar_text_cache`는 정책을 만들지 않는다(서버 전용 3개 테이블과 같은 취급 — Supabase 린터의
`rls_enabled_no_policy` INFO는 의도된 것).

---

## 10. 프런트가 붙는 자리

### 10.1 화면

| 화면 | 추가 | 데이터 |
|---|---|---|
| 작물 상세(진단 결과 → 작물 카드) | "이 지역에 맞는 품종" 섹션: 상위 3품종 카드(점수·등급·한줄이유·배지) | `/api/cultivar-score` |
| 품종 리포트(신규 화면) | §3 골격대로 렌더. 비교표는 L2, 본문은 L1, "이 땅에서는" 문단은 L3(배지) | `/api/cultivar-report` |
| 내 농사 계획 | 품종 선택 → 저장(파종 예정일 포함). 이후 체크리스트·캘린더에 품종 반영 | `my_farm.cultivar` |
| 프로필 | 저장된 품종을 지역·작물 옆에 표기 | 동일 |
| 챗봇 | 도구 2개로 자동 처리. 화면 맥락에 선택 품종 1줄 | §8.4 |

### 10.2 localStorage / `my_farm` 스키마 추가 (온라인 전환 전까지)

`For_Backend.md §5-C`의 localStorage 스키마에 필드 3개를 더한다. 기존 키는 건드리지 않는다.

```js
// myFarm: { province, sigungu, dong, crop, ... }  ← 기존
myFarm.cultivar         // '자영' | ''      품종 미선택 상태를 빈 문자열로 구분
myFarm.cultivationType  // '03002' | ''     작형 코드
myFarm.plantingDate     // '2027-05-25' | '' 파종/정식 예정일
```

### 10.3 캘린더 보정 — 품종 오프셋

`backend/chat_schedule.py`의 `CROP_SCHEDULE`(원본은 `CropAdvisor.dc.html`의 `const CROP_SCHEDULE`)은
**작물 단위**다. 품종별로 일정을 복제하지 않는다 — 복제하면 두 곳이 어긋난다. 대신:

```
품종 오프셋 = (품종 days_to_harvest 중앙값) − (작물 기준 일정의 파종→수확 일수)
  · 추백: 음수(앞당김)  → 수확·성숙기 단계의 range를 그만큼 앞으로
  · 자영: 양수(늦춤)    → 뒤로. 단 first_fall_frost를 넘으면 이동 대신 경고를 띄운다
```

파종일(`plantingDate`)이 저장돼 있으면 오프셋 대신 **실제 파종일 + 생육일수**로 단계를 재배치한다.

---

## 11. 데이터 확보 계획

### 11.1 출처 (L1 리포트 작성 근거)

| 출처 | 얻는 것 | 비고 |
|---|---|---|
| 농촌진흥청 농사로 | 작물별 품종 특성·재배관리·병해충 | 기존 `crop_standards_v2.json`과 동일 계열 |
| 국립종자원(품종보호·국가품종목록) | 등록 품종명·육성기관·등록연도 | 품종명 표기 정본 |
| 농진청 국립식량과학원/원예특작과학원 신품종 보도·품종설명서 | 신품종 특성·재배 유의점 | 자영(2009) 같은 사례 |
| 학술 논문 | 생육기간·성분·환경 반응 정량 근거 | 1호 리포트 참고문헌 형식 그대로 |
| 지역 농업기술센터 재배지침 | 지역별 파종·시비 기준 | 화면에서 "확인하세요"로 연결 |

### 11.2 1차 대상 (M-B1 범위)

**확정**: 감자 — 추백, 자영 (리포트·L2 완비. 1호)

**후보(출처 확인 후 확정)**: 아래는 후보 슬롯일 뿐이며, **국립종자원·농사로에서 품종명과 특성을
확인하기 전에는 L2에 넣지 않는다.** 확인 전 품종은 API에서 `has_report: false`로도 노출하지 않는다.

| 작물 | 후보 방향 | 채워야 하는 핵심 필드 |
|---|---|---|
| 감자 | 봄재배 주력 1종 + 가공용 1종 | `days_to_harvest`, `disease_susceptibility`, `storability` |
| 사과 | 조·중·만생 각 1종(착색·저온요구 차이가 큰 축) | `chilling_requirement` 차이, `bulking_temp`(착색·당도), `frost_sensitivity` |
| 배 | 조생·중생 각 1종 | 개화기 차이(늦서리), `chilling_requirement` |
| 오이 | 시설 주력 1종 + 노지 1종 | `growing_temp`, 노균병 감수성, 저온 신장성 |
| 상추 | 청치마 계열 1종 + 적색 계열 1종 | 고온 추대(bolting) 감수성, `growing_temp` |

> 품종명은 지역·유통 관행에 따라 별칭이 많다. `aliases[]`를 반드시 채우고, 사용자가 별칭으로
> 물어도 매칭되게 한다(예: "자영감자" → 자영).

---

## 12. 구현 단계

| 단계 | 범위 | 완료 기준 |
|---|---|---|
| **B1. 데이터 · 양식** | `data/cultivar_standards.json`(감자 2품종) · `data/cultivar_reports/감자_추백-자영.md` · `check_cultivar_consistency.py` | 검증 스크립트가 경고 0으로 통과. L1 §2 비교표가 L2에서 렌더된 값과 일치 |
| **B2. 파생 지표** | `backend/scoring/season_window.py` + `data/processed/station_season_metrics.json` 배치 | 89개 관측소 무상기간·서리일 산출, 대관령/충주/제주 값이 상식 범위(고랭지 < 중부 < 제주) |
| **B3. 점수 엔진 + API** | `backend/scoring/cultivar_fit.py`, `:8002` 엔드포인트 3개 | §6.6 표의 **의도**가 실측으로 재현(태백 자영에 게이트 발동, 평창 자영 ≥ 추백) |
| **B4. 챗봇 도구** | `chat_server.py`에 도구 2개 + 프롬프트 블록 + 축약기 | §13 골든 케이스 12개 통과, 도구 응답 토큰 상한 준수 |
| **B5. 프런트** | 작물 상세 품종 섹션 · 리포트 화면 · 품종 저장 · 캘린더 오프셋 | 품종 선택→저장→캘린더 단계 이동까지 한 흐름으로 동작 |
| **B6. L3 문단** | 배치 생성 + 캐시 + AI 배지 | 캐시 히트 시 추가 호출 0, 실패 시 L1만으로 화면 정상 |

각 단계 완료 후 이 문서에 **`## 구현 결과` 절을 덧붙인다** — 명세와 달라진 점, 검증한 것/못 한 것,
실제로 드러난 버그를 남기는 `chatbot.md §14` 관례를 따른다.

---

## 13. 검증 — 골든 케이스 12개

| # | 입력 | 기대 |
|---|---|---|
| 1 | "평창에 감자 어떤 품종이 좋아요?" | `get_cultivar_candidates` 호출, 자영·추백 점수와 근거 1~2개, 무상기간 인용 |
| 2 | "태백에 자영 심어도 돼요?" | **blockers 먼저** — 재배기간 부족 경고, "괜찮다"고 답하지 않음 |
| 3 | "추백이랑 자영 뭐가 달라요?" | 숙기·생육기간·목적·저장성 축으로 비교, 우열 단정 없음 |
| 4 | "자영 안토시아닌 얼마나 들었어요?" | 수치 제시 거부 + "재배환경에 따라 달라짐" + 성분검사 안내 |
| 5 | "자영이 항암에 좋아요?" | 효능 단정 거부, 판매 문구로 쓸 수 없음을 안내 |
| 6 | "두백 알려줘" (데이터 없음) | "아직 자료가 없어요" + 있는 품종 목록. 지식으로 지어내지 않음 |
| 7 | "딸기 품종 추천" | "안농은 5종만 다뤄요" (기존 규칙 유지) |
| 8 | "추백 언제 심어요?" | `planting_window` 인용 + 마지막 서리일 근거 + 토양검정·농업기술센터 확인 안내 |
| 9 | "씨감자 어디서 사요?" | 판매처 추천 없이 "검정된 보급종·무병 씨감자" 원칙만 |
| 10 | "추백 저장 오래 돼요?" | 저장성 낮음 + 근거(실온 1개월 발아율) + 수확 직후 판매 권고 |
| 11 | 토양 EC 결측 지역 | `reliability`='주의'를 답변에 함께 노출 |
| 12 | 품종 저장 후 "내 품종 언제 캐요?" | 화면 맥락의 선택 품종·파종일 사용, 지역을 되묻지 않음 |

추가로 **API 레벨 회귀 3개**: (a) `days_to_harvest.max=null`(자영) 처리, (b) 작형 미지정 시 최고
작형 선택, (c) 파생 지표 결측 관측소에서 가중치 재정규화 후 합이 100.

---

## 14. 리스크 & 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| **L1↔L2 수치 불일치** | 화면과 챗봇이 다른 숫자를 말함 | 검증 스크립트(§2), 비교표는 L2에서만 렌더 |
| 품종 데이터 부족(작물당 1~2종) | "추천"이라 하기 민망한 표본 | 2종 미만이면 "추천" 대신 **"품종 특성 안내"**로 문구 전환 |
| 무상기간 10년 평년의 **연변동** | 게이트 오판(가능한데 막거나, 막아야 하는데 통과) | 대표값을 위험 쪽 20퍼센타일로, 화면에 "10년 중 8년은 이보다 길어요" 병기 |
| 관측소-농지 **고도차** | 서리일·기온 오차. 산간에서 특히 큼 | `distance_km`·`station_altitude` 노출, 고도차 큰 지역은 신뢰도 '주의' |
| 품종명 별칭·중복 | 조회 실패, 엉뚱한 매칭 | `aliases[]` + 정규화(공백·'감자' 접미 제거) 후 매칭, 실패 시 목록 제시 |
| LLM이 품종 지식을 **지어냄** | 초보자가 그대로 따라 실패 | 도구 없으면 "자료 없음", 프롬프트에 명문화, 골든 케이스 6번으로 회귀 검사 |
| 기능성 성분 **표시 규제** | 표시·광고 문제로 사용자에게 실질 위험 | 함량·효능 단정 전면 금지(§8.3), 리포트 §7에 성분검사 필요 문구 고정 |
| ASOS 10년 배치 호출 비용·한도 | 지표 산출 실패 | 관측소 단위로 1회 계산해 파일 캐시, 실패 관측소는 항목 제외 + 신뢰도 표기 |

---

## 15. 안전·법적 주의 (요구사항으로 못 박는다)

1. **효능·함량 단정 금지** — 성분검사 없이 함량이나 건강 효과를 판매 문구로 쓸 수 없다는 안내를
   기능성 품종 리포트 §7에 **고정 문구로** 넣는다.
2. **농약** — 상품명·희석배수 추천 금지. "해당 작물·병해에 등록된 약제를 농약안전사용기준에
   따라" 안내만 한다(기존 챗봇 규칙과 동일).
3. **종자 유통** — 판매처·상호 추천 금지. "검정을 거친 보급종·무병 씨감자" 원칙만.
4. **파종일·시비량** — 항상 "기준"이라고 말하고, 토양검정 + 관할 농업기술센터 확인을 함께 권한다.
5. **수량·소득** — 보장·예측 표현 금지.
6. **출처 표기** — 화면의 모든 수치는 근거(`sources[].ref`)를 볼 수 있어야 한다. L3 생성 문단은
   `AI 생성` 배지를 달고, 근거 데이터를 툴팁으로 노출한다.

---

## 16. 구현 결과 (2026-08-04 · 감자 4품종)

사용자가 제공한 `potato_varieties_structured.json`(추백·자영·수미·대서)을 정본 데이터로
받아 §12의 B1~B5를 구현하고 검증했다. B6(LLM 지역맞춤 문단)은 하지 않았다.

### 16.1 만든 / 고친 파일

| 파일 | 역할 |
|---|---|
| `data/cultivars/감자.json` (신규) | L2 정본. 제공 원본을 **무손실 그대로** 넣었다 |
| `backend/scoring/cultivar_data.py` (신규) | L2 로더·정규화. 작물표준 폴백, 작형 문자열 → 작형 코드, 목적 플래그 유도 |
| `backend/scoring/season_window.py` (신규) | ASOS 10년치로 무상기간·서리일·작기 구간 통계. 연도별 원본은 `data/cache/asos_daily/`에 영구 캐시 |
| `backend/scoring/cultivar_fit.py` (신규) | 8항목 채점 + 하드 게이트 + 파종일 탐색 + 근거 문장 |
| `backend/scoring/test_cultivar_fit.py` (신규) | 단위테스트 34개(네트워크 없음, 합성 기상) |
| `backend/api/cultivar_api.py` (신규) | 4개 엔드포인트 페이로드 조립 + 30분 캐시 + 리포트 섹션 분할 |
| `backend/crop_score_server.py` | 라우트 4개 추가(:8002). 기존 `/api/crop-score`는 그대로 |
| `backend/chat_server.py` | 도구 2개(`get_cultivar_candidates`·`get_cultivar_profile`) + 축약기 + `# 품종 안내 규칙` + 화면맥락에 선택 품종·파종예정일 |
| `Beomin_web/CropAdvisor.dc.html` | 프로필 화면에 "🌱 이 지역에 맞는 품종을 골라봤어요" 카드(순위·파종창·근거·**항목별 적합도 막대**·조회 실패 안내+다시 시도), `loadCultivars()`·`reloadCultivars()`·`pickCultivar()`·`toggleCultivarDetail()`, 챗봇 맥락에 품종 |
| `data/scripts/check_cultivar_consistency.py` (신규) | L1↔L2 정합성 검사 |
| `data/cultivar_reports/감자_추백-자영.md` | 헤더의 L2 경로를 실제 경로로 수정 |

### 16.2 제공 데이터 → 채점 필드 대응 (폴백 포함)

| 제공 원본 필드 | 채점에서 쓰는 값 | 없을 때 |
|---|---|---|
| `growth_period_days{min,max}` | 생육일수(최소값 기준) | — (없으면 그 품종은 채점 불가) |
| `growth_period_days{spring,summer}` (대서만) | **작형별** 생육일수(봄 90~100 / 여름 110) | 위 대표값 사용 |
| `recommended_environment.tuber_bulking_temperature_c` | 비대 적온 | 작물표준 `tuber_bulking_optimal`(15~18℃) |
| `recommended_environment.soil_ph` | 토양 산도 기준 | 작물표준 `soil.ph.optimal`(5.0~6.0) — 자영·수미·대서가 이 경로 |
| `recommended_environment.recommended_season[]` | 작형(봄재배·고랭지재배·가을재배) | 봄재배만 후보 |
| `not_recommended_season[]` | 작형 **제외**(수미 가을재배) | — |
| `disease_and_pest_risks[].risk_level` | 병해 감점(높음 10 / 중간·관리필요 5 / 낮음 1) | 항목 제외 |
| `physiological_disorders[].causes`에 '고온' | 고온일수 감점 ×1.4 (자영·대서) | 배수 1.0 |
| `category`/`tuber_characteristics.special_component`에 기능성·안토시아닌 | **후기저온 항목 활성**(자영) | 항목 제외 |
| `category`/`primary_use`에 '조기 출하' | **출하시기 항목 활성**(추백) | 항목 제외 |
| `recommended_for_beginner` | 배지 + 동점 정렬 | — |
| 작물표준 `high_temp_risk.threshold`(25℃) | 고온일수 기준 | 25℃ 고정 |

원본에 없어서 **쓰지 않은** v0.1 설계 필드: `drainage`/`waterlogging_sensitivity`(수치 없음 →
집중강수일수로 대체), `altitude_pref`(자영 서술만 → 고랭지 작형 성립 판정으로 대체),
`confidence`/`sources[]`(제공 데이터에 없음 → `dataset.source_scope`·`caution`을 그대로 노출).

### 16.3 명세와 달라진 점 — 전부 실제 결과가 틀려서 고친 것이다

| 고친 것 | 왜 |
|---|---|
| 항목 6개 → **8개**(파종·출현기, 출하시기 추가) | 파종기 조건이 없어 **제주 가을감자를 7월 하순(평균 27℃)에 심으라**고 했다. 출하시기가 없어 추백을 7월에 캐라고 하면서도 만점을 줬다 |
| 재배기간 = 무상기간 여유 **∩ 수확→첫서리 여유** | 여유가 한쪽만 있으면 탐색이 늘 '가장 늦은 파종일'을 골랐다(늦게 심을수록 비대기가 서늘해지므로). 서리 여유를 점수로 갚게 하니 관행 파종기로 수렴 |
| 고랭지 작형 조건에서 **클러스터 0(중산간내륙형) 제외** | 이 클러스터에 충주(115m) 같은 평난지가 24개소 들어 있어, 충주에 고랭지 여름재배를 권했다. 표고 400m↑ 또는 클러스터 2(고랭지형)만 인정 |
| 가을재배 파종기 07-05~09-10 → **08-01~09-10** | 7월 파종은 가을재배가 아니라 여름재배다(장마 직격) |
| 비대온도 감점 완화(4℃에 55점, 고온일수 계수 ↓) | 초기 계수로는 국내 표준 재배법인 봄재배가 전 지역에서 0~47점이었다(수미 충주 0점) |
| 파종기 고온을 **작형별 게이트**로 | 봄·고랭지는 26℃↑에서 상한 60, 가을재배는 29℃↑만 막는다. 가을재배에 봄 기준을 대면 남부·제주의 실재하는 주력 작형이 사라진다 |
| `cautions`(계산)와 `variety_warnings`(품종 일반)를 분리 | 합쳐 보내니 챗봇이 품종 일반 경고('재배기간 부족에 주의')를 이 지역 판정 결과처럼 말했다 |
| 리포트 대조 대상을 **파일명**으로 한정 | 추백·자영 리포트 본문에 '수미'가 언급돼, 수미가 남의 리포트와 대조되어 검사가 헛통과했다 |
| 서리일 통산일수를 평년(2001) 기준으로 환산 | 표본에 섞인 윤년 개수만큼 대표 서리일이 하루 밀렸다(단위테스트가 잡았다) |
| 무상기간 대표값 = **짧은 쪽 20퍼센타일** | 평균을 쓰면 10년 중 절반이 그보다 짧다 → 만생종을 "가능하다"고 잘못 말한다 |

### 16.4 실측 결과 (5개 지역 · ASOS 10년 평년)

| 지역(관측소·표고) | 무상기간 | 1위 | 2위 | 3위 | 4위 |
|---|--:|---|---|---|---|
| 충주 주덕읍 (충주·115m) | 198일 | **추백 91.0** 봄 3/20→6/08 | 수미 83.1 | 대서 81.6 | 자영 71.6 |
| 평창군 (정선군·312m) | 183일 | **추백 83.7** 봄 4/04 | 대서 79.4 | 자영 79.2 고랭지 6/09→9/27 | 수미 78.9 |
| 태백시 (태백·714m) | 169일 | **추백 82.0** 봄 4/13 | 자영 81.7 고랭지 6/06→9/24 | 대서 79.0 고랭지 | 수미 78.4 |
| 서귀포시 (서귀포·52m) | 309일 | **추백 97.0** 봄 2/20→5/11 | 대서 94.9 | 수미 94.3 | 자영 86.1 가을 8/01→11/19 |
| 보성군 (장흥·44m) | 210일 | **추백 88.9** 봄 3/24 | 수미 82.2 | 대서 81.4 | 자영 69.3 |

리포트(§L1)의 서술과 방향이 일치한다 — 남부·평난지는 추백 조기재배, 고랭지는 자영·대서
만생종(여름재배), 제주는 자영 가을재배(후기저온 100점), 남부 봄재배의 자영은 비대기 고온으로
불리(충주 비대온도 3.6점 → 봄재배 71.6). 태백에서 추백이 1위인 것은 4/13 파종 봄재배가
실제로 성립하기 때문이며, 자영 고랭지(81.7)와 0.3점 차이라 카드에 둘 다 보인다.

### 16.5 검증한 것 / 못 한 것

**검증**
- 단위테스트 34개 통과(`backend/scoring/test_cultivar_fit.py`, 네트워크 없이 합성 기상).
  게이트·조건부 항목·작형 성립성·가중치 재정규화·결측 처리·순수 함수 단조성.
- 정합성 검사 통과: 불일치 0건 / 경고 4건(추백·자영 비대적온이 작물표준과 다름 = 품종값 우선,
  수미·대서는 대조할 리포트 없음).
- 엔드포인트 4개 실호출(임시 포트 8013, 기존 8002를 건드리지 않고).
- **실제 브라우저(Chromium)로 프로필 화면 렌더 확인** — 카드에 4품종·점수·등급·작형·
  파종~수확창·근거·배지가 표시되고, "이 품종으로 정하기"를 누르면
  `localStorage.beomin_my_farm`에 `{cultivar:"추백", cultivationType:"봄재배", plantingDate:"2027-03-20"}`이
  저장되고 버튼이 "✅ 내 품종으로 저장됨"으로 바뀐다.
- **홈 → 프로필 전 구간(E2E)**: 지도가 보내는 것과 동일한 `postMessage({type:'selectRegion', …})`로
  충주 주덕읍을 고르고, 홈 적합도 카드에서 감자의 "🌱 나의 작물로 선택"을 누르면 프로필로
  이동하며 품종 카드가 그 지역 기준으로 채워진다(추백 91.0 → 수미 83.1 → 대서 81.6 → 자영 71.6).
  `myFarm`은 `감자 / 충청북도 충주시 주덕읍`으로 저장된다.
- **항목별 적합도 막대**: "📊 항목별 적합도"를 누르면 8항목이 점수·가중치와 함께 막대로 펼쳐진다
  (추백 충주 기준: 재배기간 100 · 파종·출현 98 · 비대기 온도 72 · 토양 88 · 비·과습 100 ·
  병해 위험 90 · 출하 시기 100). 첫 구현은 한 줄 레이아웃이라 사이드바 폭(약 220px)에서
  `flex:1` 트랙이 **0px로 접혀 막대가 보이지 않았다** — 라벨/점수와 막대를 두 줄로 분리해 고쳤다.
- **조회 실패 상태**: 구버전 서버(:8002)가 404를 주면 카드가 조용히 사라지던 문제를 고쳤다.
  이제 "품종 적합도 API가 응답하지 않아요(404). 점수 서버를 최신 코드로 다시 시작해 주세요"
  + **다시 시도** 버튼을 보여준다(서버를 켠 뒤 새로고침 없이 재조회). 감자 외 작물처럼
  "품종 데이터가 아직 없어요"는 정상 상태라 카드를 띄우지 않는다.
- 챗봇 도구 축약 크기: `get_cultivar_candidates` 약 1,000자(≈450토큰) / `get_cultivar_profile`
  약 250자. `blockers`는 축약에서 절대 빠지지 않게 했다.

**못 한 것**
- LLM 실대화 골든 케이스(§13) — API 키를 쓰는 실호출 검증은 하지 않았다. 도구 함수·프롬프트·
  맥락 주입까지만 확인했다.
- L3 지역맞춤 문단(B6) 미구현. 리포트 화면은 L1 원문 + L2 수치까지만.
- Supabase `my_farm`에 품종 컬럼 추가(§9의 `alter table`) 미적용 → 품종 선택은 **기기
  localStorage에만** 남는다. `farmRow()`가 컬럼을 화이트리스트로 만들어서 저장 자체는 안전하다.
- 감자 외 4작물(사과·배·오이·상추) 품종 데이터 없음 → API가 `available_crops: ["감자"]`로 안내.
- 제주 **월동재배**(12~1월 파종, 해를 넘기는 작기)는 채점 범위 밖이다. 작기가 연도 경계를
  넘으면 `window_metrics`가 12/31에서 잘린다(`truncated_years`로 표시).

### 16.6 실행 · 반영 방법

```bash
# 1) 점수 서버 재시작 (새 라우트 4개가 여기 붙어 있다)
python backend/crop_score_server.py            # :8002

# 2) 챗봇 서버 재시작 (도구 2개 추가)
python backend/chat_server.py                  # :8003

# 3) 확인
curl "http://localhost:8002/api/cultivar-score/감자?region=충청북도%20충주시%20주덕읍"
python data/scripts/check_cultivar_consistency.py
cd backend/scoring && python -m pytest test_cultivar_fit.py -q
```

> ⚠️ 첫 조회는 관측소 10년치 ASOS를 받아오느라 20~40초 걸린다. 받은 일자료는
> `data/cache/asos_daily/`에 영구 캐시되어 두 번째부터는 2~3초다. 화면은 그 사이
> "과거 10년 기상자료로 품종을 맞춰보는 중이에요…"를 보여준다.

---

## 17. 구현 결과 2차 (2026-08-04 · 5작물 18품종으로 확장)

`plant_breed/` 로 사과·배·오이·상추 품종 데이터가 들어와 §16(감자 전용)을 5작물로 넓혔다.
`data/cultivars/{사과,배,오이,상추}.json` 추가(감자는 기존 파일과 내용 동일해 그대로 둠).

### 17.1 작물별로 채점 엔진을 갈랐다 — 같은 필드가 같은 뜻이 아니다

`cultivar_data.CROP_SCORING_MODE`로 나눈다.

| 모드 | 작물 | 엔진 |
|---|---|---|
| `climate` | 감자 | `cultivar_fit.score_cultivars` (§16 그대로. 회귀 없음) |
| `conditions` | 사과·배·오이·상추 | `cultivar_conditions.recommend` (신규) |

4작물을 기존 기후 채점에 태울 수 없었던 이유는 **데이터가 그 축을 지지하지 않기** 때문이다.
필드 이름이 같아서 그대로 넣으면 조용히 틀린다.

| 작물 | `growth_period_days`의 실제 의미 | 넣었으면 생겼을 일 |
|---|---|---|
| 감자 | 파종~수확 일수 `{min,max}` | (정상) |
| 사과·배 | **만개후일수** (후지 188~204, 신고 155~165) | 무상기간 게이트가 오작동 — 후지가 평창(183일)에서 "재배 불가" |
| 오이·상추 | 육묘일수 + 정식후일수를 작형별로 쪼갠 형태 | 파종~수확이 아예 안 나옴 |

그래서 `_growth_days()`가 `metric`에 '만개'가 들어가면 `growth_days`가 아니라
`bloom_to_harvest`로 담는다. 오이·상추는 `sowing_to_harvest_total_days_estimate`만 쓴다.

추가로 사과의 품종별 `recommended_environment`는 **수치가 아니라 산문**이다(후지는
"후지 전용 재배환경 수치는 확인하지 못했다"고 스스로 밝힌다). 수치는
`common_management.environment`에만 있어 `_merged_env()`로 보충하는데, 그 결과 5품종의
기후 수치가 전부 같아진다 — 기후로 순위를 내면 5품종이 동점이 된다는 뜻이다.

### 17.2 `confidence` 라벨을 채점에서 존중한다

원본이 수치마다 `확실 / 보통 / 불확실 / 확인 불가`를 직접 적어 두었다. `불확실` 이하는
채점에 쓰지 않고(`growth_days_scorable=False`) 서술로만 신뢰도와 함께 노출한다(§6.7).
라벨이 아예 없으면(감자) 종전대로 쓴다 — 그러지 않으면 감자 채점이 통째로 죽는다.

- 로메인상추: 파종~수확 60~90일이 `불확실` → 채점 제외
- 가시오이: 생육기간 `확인 불가`, 데이터가 "그대로 적용하지 않는다"고 명시 → 수치 없음
- 사과·배 만개후일수: 거의 전부 `불확실(추정치)` → 화면에 "(추정)" 표기

### 17.3 지역 신호 — 첫서리로 짰다가 걷어냈다

화면 제목이 "이 지역에 맞는 품종 추천"이라 지역이 결과를 바꿔야 한다. 처음에는
**수확기 vs 첫서리**로 짰는데 **청송에서 후지가 부적합으로 찍혔다**. 국내 최대 후지
주산지다. 첫서리는 일최저 0℃ 첫날일 뿐이고 사과 성목 내한성은 -30℃이며,
`flower_frost_threshold_by_stage`는 **봄 개화기** 기준이지 가을 수확기가 아니다.

바꾼 축은 **착색기 기온**이다. `coloring_daily_mean_c`(12~13℃)를 기준으로, 품종별
수확기 30일 전 구간의 ASOS 일평균을 견준다. 수확기가 품종마다 다르므로(쓰가루 8월 /
후지 10월 하순) 같은 지역에서도 품종별로 값이 갈린다.

| 지역 | 후지 착색기 일평균 | 적온 초과 |
|---|---|---|
| 평창 | 14.9℃ | +1.9℃ |
| 청송 | 15.4℃ | +2.4℃ |
| 나주 | 19.0℃ | +6.0℃ |

**한계를 화면에 밝힌다.** 12~13℃는 사실상 만생종 기준이라 이 정렬은 늦게 따는 품종이
앞선다. 처음엔 초과분을 그대로 감점했더니 조생종 쓰가루(착색기 7~8월, 25℃)가 "착색
불량"으로 찍혔다 — 품종의 특성을 결함으로 표시하는 셈이다. 그래서 기온은 정보로만 보여주고,
**데이터가 그 품종의 약점으로 직접 밝힌 경우에만** 주의로 올린다(홍로: 착색 최적 25℃와
고온 취약 연구 근거가 데이터에 있다). 정렬 기준의 한계도 작물 주의문에 적어 둔다.

배는 착색 적온 수치가 데이터에 없어 지역 판정을 건너뛴다. 오이·상추는 주력 작형이
시설(촉성·반촉성)이라 노지 기상으로 판정하지 않고 "작형 선택이 지역보다 먼저"를 밝힌다.

### 17.4 추천 가능 집합을 하드 제한했다

추천은 `data/cultivars/<작물>.json`의 18품종으로만 한다(`cultivar_data.is_recommendable`).
작물 일반 지식(`crops_for_llm.json`)의 `major_varieties`에는 감자만 24품종이 들어 있는데
우리가 특성을 검수한 목록이 아니다. 검증: `조풍`·`남서`·`아리수` 모두 차단됨.

### 17.5 프런트엔드는 고치지 않았다

기존 UI가 이미 관용적이라 계약만 맞추면 됐다.
`score`가 숫자가 아니면 `'-'`, `planting_window.from/to`가 없으면 파종~수확 줄을 렌더하지
않는다. 조건 모드는 `score: None`·`planting_window: {}`로 나가고, 근거는
`reasons`/`cautions`/`badges`/`grade_label`로 전달한다.

단 `maturity`는 감자가 문자열인데 사과·배는 `{class, harvest_period}` dict여서
`{{ cv.maturity }}`에 `[object Object]`가 찍혔다. `_maturity_text()`로 문자열화한다
("만생 · 10월 하순~11월 상순").

### 17.6 배포 경로를 열었다 — 이전까지 동작하지 않았다

§16 구현 이후에도 `api/`에 함수가 없고 `vercel.json`에 rewrite가 없어 **배포에서는 감자
품종 추천조차 호출되지 않았다**(응답이 없으면 `cultivarBox.show`가 `false`가 되어 카드가
렌더되지 않아 화면상 드러나지 않았다). 이번에 추가했다.

- `api/cultivar_score.py` → `/api/cultivar-score/:crop?region=`
- `api/cultivars.py` → `/api/cultivars/:crop`
- `vercel.json` rewrite 2개. `includeFiles`가 이미 `data/**`를 포함해 데이터는 자동 반영.

### 17.7 검증

| 항목 | 결과 |
|---|---|
| 5작물 `load_crop` · 18품종 이름 일치 | OK |
| 감자 채점 회귀(평창·김제·제주 3지역, 파종/수확일·항목점수) | **변경 전과 완전 동일** |
| 사과·배 만개후일수가 `growth_days`로 새지 않음 | 위반 0건 |
| `maturity`가 전부 문자열 | 위반 0건 |
| 조건 모드 응답 계약(필수 10필드 · score None) | 위반 0건 |
| 추천 가능 집합 가드 | 미검수 품종 3종 차단 확인 |
| `py_compile` 5파일 · `api/` 함수 8개 import | 전부 OK |

### 17.8 못 한 것

- **상추 기후 채점**: `crop_standards_v2[상추].bolting_risk`에 "25℃에서 파종 10일 만에 추대,
  20℃ 20일, 15℃ 30일"이라는 정량 관계가 있어 고온 추대 축을 만들 수 있다. 다만 3품종 중
  로메인이 `불확실`이라 근거가 고르지 않아 이번엔 조건 모드에 뒀다.
- **과수 저온요구시간**: 사과 1200~1500h(7℃ 이하), 배 7.2℃ 기준값이 있으나 시간 단위
  누적에는 시간별 기온이 필요하다. ASOS 일자료로는 계산하지 않았다.
- **수분수 궁합**: 사과 `pollination.s_genotype`과 후지-감홍 불화합 쌍이 데이터에 있는데,
  "이미 심은 품종"을 입력받는 자리가 없어 경고로만 노출하고 판정에는 쓰지 않았다.
- **챗봇 연동**(§8): `crops_for_llm.json`(5작물 균일 15필드) 물리기와 도구 2개 미구현.
- 오이 `selection_guide`(조건→품종군→이유)를 응답에 담기만 하고 화면에서 쓰지 않는다.

---

## 부록 A. 참고한 기존 자산

| 자산 | 이 기능에서의 역할 |
|---|---|
| `crop_standards_v2.json` | 품종 값의 **폴백 기반**(작물 표준). 품종 파일은 차이만 적는다 |
| `backend/services/live_scoring.py` · `crop_score_server.py`(:8002) | 작물 점수·토양·위험신호 재사용, 품종 엔드포인트가 붙는 서버 |
| `backend/api/asos.py` | 파생 지표(무상기간·구간 기온·강수) 원천 |
| `backend/api/weekly_fcst.py` · `midfcst.py` | 파종 시점 임박 시 "올해는 이렇다" 보조 정보 |
| `data/processed/region_cluster_map.json` (K=6, 89개소) | 품종 사전 필터 + 지역 성격 설명 |
| `backend/chat_schedule.py` (`CROP_SCHEDULE`) | 품종 오프셋을 적용할 기준 일정 |
| `chatbot.md` §4·§5·§6·§9 | 프롬프트/맥락/도구 축약/비용 규약 — 그대로 승계 |
| `DB.md` §4.3·§4.6·§4.7 | 테이블·RLS 관례, `my_farm` 확장 지점 |
| `PRD.md` §7 | 점수 함수(완만 감점)·가중 종합 방식 |

## 부록 B. 용어

| 용어 | 뜻 |
|---|---|
| 품종(cultivar) | 같은 작물 안에서 육성·등록된 계통. 이 문서의 채점 대상 |
| 작형 | 재배 시기 유형(봄재배·가을재배·고랭지·월동 등). `crop_codes` 재사용 |
| 무상기간 | 봄 마지막 서리일 ~ 가을 첫 서리일 사이 일수. 재배 가능 기간의 상한 |
| 숙기 | 파종에서 수확까지 걸리는 정도(극조생~만생) |
| 하드 게이트 | 완만 감점이 아니라 점수 상한을 씌우는 판정(실패를 '조금 불리'로 보이지 않게) |
| L1/L2/L3 | 지식 원문 / 구조화 필드 / LLM 지역맞춤 문단 (§2) |

---

## 18. 구현 결과 3차 — 추천 근거(pros·cons) + 역병 (2026-08-04)

"이 품종을 왜 추천하는가"를 사용자가 납득할 수 있게 만들고, 품종별 역병 위험·대처를 붙였다.

### 18.1 근거를 목록으로 바꿨다 — 카드가 한 줄만 보여주고 있었다

기존 카드는 `reasons[0]`과 `cautions[0]` **각 한 줄씩만** 렌더했다. 서버는 여러 개를
보내는데 화면이 첫 줄만 쓴 것이다. 그래서 "왜 이 품종인가"가 전달되지 않았다.

`backend/scoring/cultivar_reasons.py`(신규)가 pros·cons를 만든다. **각 항목에 근거 출처
(basis)를 붙인다** — 문장만 보여주면 사용자가 확인할 방법이 없어 납득이 아니라 신뢰
요구가 된다.

| pros 출처 | cons 출처 |
|---|---|
| 지역 기상(감자=파종~수확 성립, 사과=착색기 기온) | 지역 기상(차단·주의) |
| `recommended_for_beginner` + 이유 | 역병(별도 자료) |
| `selection_conditions` | `disease_and_pest_risks` 위험 '높음' |
| `storage.ability` + 저장일수 | `key_warnings` |
| `primary_use`/`market_use` | 사과 `risks` / 오이 `specific_risks` |
| | `confidence`가 낮은 추정치 |

두 엔진이 같은 빌더를 쓴다(`cultivar_fit`·`cultivar_conditions`). 문구가 갈리지 않게.

### 18.2 근거 없는 판정을 지어내고 있었다 (수정)

`_normalize_variety`가 `beginner_friendly`를 `bool(raw.get("recommended_for_beginner"))`로
만들었다. **사과·배에는 이 필드가 아예 없어** `bool(None)=False`가 되고, 그 결과 사과
5품종 전부에 "초보자에게는 손이 많이 가요"가 **데이터 없이** 붙었다.

없음(None)과 False를 구분하도록 고쳤다. 배지(`초보자에겐 소규모 시험재배 권장`)도
같은 이유로 `is False`일 때만 붙인다.

### 18.3 역병 — 18품종 중 위험도가 실제로 조사된 것은 4개뿐이다

`data/late_blight.csv`(신규) + `backend/scoring/blight_data.py`(신규).

| 판정 | 품종 |
|---|---|
| 위험 등급 있음 | 감자 수미(높음) · 자영(관리 필요) · 대서(중간) |
| 관리 대상으로만 기재 | 사과 홍로 |
| **자료 없음** | 나머지 14품종 |

`documented_in_dataset=N`인 행의 위험도 칸에는 '확인 불가', '공통주의(개별 위험 미기재)'
같은 말이 들어 있다. 이걸 등급처럼 화면에 띄우면 조사하지 않은 위험을 판정한 것처럼
보이므로 `assessed` 플래그로 갈랐다. 화면 배지도 '자료 없음'으로 구분해 보여준다.

**상추·배는 "역병 자료 없음"이 정답이다.** CSV `note`가 짚어 준다 — 상추의
노균병·무름병·뿌리썩음병, 배의 검은무늬병·검은별무늬병은 역병(Phytophthora)과
병원균이 다르다. 있는 병해 자료를 역병으로 옮기면 농가가 엉뚱한 약제를 쓴다.

### 18.4 살균제 성분명을 걷어냈다

원본 사과 행의 `management`에 `metalaxyl·cyazofamide·amisulbrome·cymoxanil·dimethomorph`가
나열돼 있다. 약제는 작물·병해별 등록이 다르고 안전사용기준이 붙으므로 초보에게 성분명만
던지면 미등록 약제를 쓰게 된다. 로더에서 목록을 제거하고 표준 안내로 바꾼다.

판정을 두 번 고쳤다.
1. 처음엔 괄호 안을 라틴 문자 클래스로 잡았는데, 원문이 `(metalaxyl·…·dimethomorph 등)`
   처럼 한글 '등'을 섞어 써서 **매칭이 안 됐다**(유출 5건).
2. 길이 기준(4자 이상 영문 낱말 2개)으로 바꾸니 `(疫病, Late Blight)` 같은 **병명 병기가
   지워졌다**. 성분명은 관례상 전부 소문자라는 점으로 갈랐다(소문자 6자 이상 2개 이상).

임계값을 1로 낮추지 않은 이유: 학명의 종소명이 소문자다(`(Phytophthora cactorum)`의
cactorum). 대가로 성분이 하나만 적힌 경우는 통과한다 — 현재 데이터에는 없다.

### 18.5 화면·챗봇

- 카드: `이 품종을 추천하는 이유` / `고르기 전에 볼 점` 목록 + `역병` 블록(배지·증상·대처·비고).
  `cautions[0]` 한 줄은 없앴다(cons가 같은 내용을 담아 중복이었다).
  차단(blockers)은 별도로 맨 아래 강조 유지 — '심으면 실패하는' 사유는 다른 주의와 섞지 않는다.
- 배지에 긴 문장이 들어가 줄바꿈되는 꼴이 나서 `badge_text`(짧은 라벨)와
  `status_text`(한 줄 설명)를 분리했다.
- 챗봇 `get_cultivar_candidates`: `reasons`·`cautions`·`variety_warnings` 세 목록을
  `추천이유`·`고려할점`으로 합치고 `역병`을 따로 실었다. 세 목록이 같은 말을 나눠 갖고
  있어 토큰만 먹고 답변이 산만했다. 응답 1,665자(≈832토큰).
- 시스템 프롬프트에 역병 규칙 추가: '자료 없음'은 위험이 없다는 뜻이 아니라는 것,
  역병과 다른 병을 섞지 말 것, 약제 성분명을 말하지 말 것.

### 18.6 검증

| 항목 | 결과 |
|---|---|
| 18품종 pros·cons 존재 + basis 부착 | 위반 0건 |
| 데이터 없는 초보 판정 생성 | 0건 (사과·배 `beginner_friendly=None`) |
| 살균제 성분명 유출 | 0건 (한글 괄호·병명 병기·학명은 보존) |
| 감자 채점 회귀 | 파종·수확일·작형·항목점수 **불변** (토양 항목 제외는 흙토람 429 변동) |
| 브라우저 실제 렌더(Playwright) | 4품종 카드에 pros·cons·역병 전부 표시 확인 |
| 챗봇 도구 응답 크기 | 1,665자(≈832토큰) |

---

## 19. 프로필 흐름 변경 — 품종을 고른 뒤 농업일지 (2026-08-04)

### 19.1 무엇이 문제였나

품종 추천 카드가 **농업일지 한가운데 묻혀 있었다.** 프로필 탭 순서가
`그림카드 → 캘린더 → 수분상태 → 7일예보 → 품종추천 → 체크리스트`였다.

그리고 품종을 골라도 **캘린더 날짜가 하나도 바뀌지 않았다.** `pickCultivar()`가
`myFarm.cultivar`만 저장하고 계획은 작물 기준 그대로 남겼다.

### 19.2 바꾼 흐름

```
지역·작물 선택 → 프로필
  ① 나의 작물 그림카드 (고른 품종·일정 반영 내용 표시)
  ② 이 지역에 맞는 품종 카드 (추천 순위·근거·역병) → "이 품종으로 정하기"
  ③ 그 품종 기준 농업일지
```

품종 미선택 상태에서는 일지를 감추고 안내를 보여준다. 고르기 전 일지는 '작물 평균'일
뿐인데, 그걸 먼저 보여주면 사용자가 그 날짜를 자기 일정으로 받아들인 뒤 품종을 골라
날짜가 바뀌는 혼란이 생긴다.

구현: `cultivarBox`를 `farmPlanCards` sc-for 밖으로 끌어내 `cultivarPick`으로 최상위
노출했다(단일 농장 모델이라 카드가 하나뿐이므로 60줄 조립을 복제하지 않았다).
`hasFarmPlan`에 `savedCultivar` 조건을 더하고 `needsCultivarPick`을 추가했다.

### 19.3 품종별 농업일지 생성 (§10.3 구현)

`applyCultivarOffset()`이 단계 날짜를 옮긴다. **근거가 작물마다 달라 모드를 나눴다.**

| 모드 | 작물 | 근거 | 예 |
|---|---|---|---|
| `planting` | 감자 | `planting_window`(ASOS 실측 권장 파종일 + 생육일수) | 추백 수확 6/23 · 수미 7/03 · 자영 9/27 |
| `harvest` | 사과·배 | 품종 `maturity.harvest_period` | 후지 수확 단계 9/1~11/15 → **10/21~11/10** |
| `none` | 오이·상추 | 품종별 날짜 근거 없음 | 작물 기준 유지 + 그 사실을 화면에 밝힘 |

`planting` 모드는 파종일 차이(shift)와 생육일수 차이(stretch)를 뒤 단계로 갈수록
크게 준다. 오프셋을 **날짜마다 다시 계산**하는 것이 중요하다 — 단계의 시작일로 구한
값을 종료일에도 쓰면 순서가 깨진다(실제로 추백에서 제초가 7/20까지인데 수확이 6/23에
시작하는 결과가 났다).

`pickCultivar()`가 계획을 다시 만들고, `loadCultivars()` 응답이 늦게 도착한 경우
(새로고침 직후)에도 한 번 더 맞춘다. 이걸 빼면 품종을 골라 뒀는데 새로고침하면 날짜가
작물 기준으로 되돌아간다.

### 19.4 조건 모드 작물에서 드러난 표시 버그 2개 (수정)

브라우저로 오이 화면을 보고 찾았다. 감자만 보면 드러나지 않는다.

- `무상기간 undefined일 · 늦서리 ? · 첫서리 ? · 관측소 천안(0년 평년)` — 조건 모드는
  서리 자료를 쓰지 않아 `region_metrics`에 무상기간이 없는데 그대로 찍었다.
  있는 조각만 이어 붙이도록 고쳤다 → `관측소 천안`.
- `초보 추천 -` / `반촉성재배 ·` — 템플릿에서 `{{gradeLabel}} {{score}}`,
  `{{season}} · {{maturity}}`로 이어 붙여, 점수·숙기가 없는 작물에서 빈 자리와 점이
  남았다. JS에서 `gradeText`·`metaText`로 미리 합치도록 고쳤다.

조사 오류도 고쳤다("청치마상추은" → "청치마상추는"). 받침 판정 도우미
`withParticle()`을 뒀다(파이썬 쪽 `cultivar_reasons.with_particle`과 같은 규칙).

### 19.5 검증 (Playwright · 실제 브라우저)

| 항목 | 결과 |
|---|---|
| 품종 미선택 → 일지 숨음 | 캘린더 '오늘' 버튼 0개 · 안내 표시 |
| 품종 카드가 그림카드 뒤(캘린더보다 위) | y=595 < 안내 y=2650 |
| 선택 → 일지 표시 | 캘린더 '오늘' 버튼 1개 · 제목 "…수미 키우는 농업일지" |
| 품종별 날짜 차이 | 추백 파종 4/04·수확 6/23 / 수미 4/04·7/03 / 자영 6/09·9/27 |
| 단계 순서 겹침 | 3품종 모두 0건 |
| 사과 harvest 모드 | 후지 수확 10/21~11/10 |
| 오이·상추 none 모드 | 작물 기준 유지 + 안내 문구 |

---

## 20. 배·상추·오이 체크리스트 세부 수치 보강 (2026-08-05)

### 20.1 먼저 측정했다 — 전제가 달랐다

"배·상추·오이를 감자만큼 상세하게"라는 요청이었는데, 재 보니 **비율로는 이미 감자보다
나았다.** 체크리스트 항목은 `CROP_SCHEDULE` 단계의 작업명을 `&`로 쪼갠 것이고, 항목마다
`taskHowTo()`가 (1) 일반 순서 `steps` (2) 그 작물 `cultivationGuide`에서 뽑은 세부 수치
`cropLines`를 붙인다. 보강 전 실측:

| 작물 | 항목 | 수행방법 있음 | **세부 수치 있음** |
|---|---|---|---|
| 감자 | 48 | 92% | **54%** |
| 배 | 15 | 100% | 73% |
| 상추 | 37 | 95% | 81% |
| 오이 | 36 | 100% | 78% |
| 사과 | 14 | 100% | 21% |

단계당 항목 수(감자 2.0 / 배 1.9 / 오이 1.8 / 상추 1.5)와 note 길이(배 119자 > 감자 57자)도
비슷하거나 배가 더 길었다. 진짜 구멍은 **특정 작업에 그 작물 수치가 아예 없다**는 것이었다.

| 작물 | 세부 수치가 없던 항목 |
|---|---|
| 배 | 발아기 점검 · 배수 관리 · 수확 · 저장 관리 |
| 상추 | 물주기 (+ '고온기 빛가림'은 수행방법 자체가 없었다) |
| 오이 | 병해충 방제 · 수확 |

### 20.2 채운 방법

`data/crops_for_llm.json`(농업기술길잡이 5권)에서 해당 내용을 가져와 각 작물
`cultivationGuide.blocks`에 추가했다. **`taskHowTo`의 `find` 검색어에 걸리도록** 낱말을
그대로 담는 것이 관건이다(`guideLinesFor`가 블록 제목·본문을 그 검색어로 훑는다).

| 작물 | 추가한 블록 | 걸리는 검색어 |
|---|---|---|
| 배 | 발아기 점검·언피해 대비 / 배수로·토양 물빠짐 관리 / 수확 후 관리·분산수확 / 예비저장·저온저장고 관리 | `발아` `배수로,물빠짐` `수확 후 관리` `예비저장,저온저장고` |
| 상추 | 물주기 간격과 양 / 고온기 차광(빛가림) | `물주기` `차광,빛가림` |
| 오이 | 병해충 예방과 방제 / 수확 후 관리·수확 크기 | `병해충` `수확 후 관리` |

'고온기 빛가림'은 매칭되는 `TASK_HOWTO`가 없어 항목에 '어떻게 하나요?'가 아예 붙지
않았다. `match: ['빛가림','차광']` 항목을 새로 넣었다(다른 작물의 차광 작업도 걸린다).

약제는 성분명·상품명을 쓰지 않았다 — 작물·병해별 등록이 달라 초보에게 성분명만 주면
미등록 약제를 쓰게 된다. "농약안전정보시스템에서 확인" 원칙만 담았다.

### 20.3 결과

| 작물 | 항목 | 수행방법 | 세부 수치 | 평균 수치 줄 |
|---|---|---|---|---|
| 배 | 15 | 100% | **100%** (73→100) | 2.9 |
| 상추 | 37 | **100%** (95→100) | **100%** (81→100) | 3.1 |
| 오이 | 36 | 100% | **100%** (78→100) | 3.1 |

검증(Playwright 실제 브라우저): 캘린더에서 날짜를 눌러 체크리스트를 띄우고 '어떻게
하나요?'를 펼쳐, 세 작물 모두 '○○ 기준 세부 수치' 블록이 실제로 렌더되는 것을 확인했다.
(상추는 촉성재배 계획이 이듬해 1월 시작이라 ▶로 달을 넘겨야 이벤트가 나온다.)

### 20.4 이제 감자·사과가 가장 얇다

요청 범위(배·상추·오이) 밖이라 손대지 않았지만, 보강 결과 **기준으로 삼았던 감자가 가장
얇아졌다.**

- 감자: 세부 수치 54% · 수행방법 없는 작업 4종(건조제 살포·시설보온·관수·시설환기) ·
  수치 없는 작업 6종(밭 준비·밑거름 시비·제초·배토(북주기)·병해충 방제·배수 관리)
- 사과: 세부 수치 21% · 수치 없는 작업 11종

같은 방식(crops_for_llm.json → cultivationGuide 블록 추가)으로 채울 수 있다.

---

## 21. 감자·사과 체크리스트 세부 수치 보강 — 5작물 전부 100% (2026-08-05)

§20에서 배·상추·오이를 채운 뒤 **기준으로 삼았던 감자가 가장 얇아졌다.** 사과는 더
얇았다(14항목 중 3개만 수치). 같은 방식으로 채웠다.

| 작물 | 항목 | 수행방법 | 세부 수치 | 평균 수치 줄 |
|---|---|---|---|---|
| 감자 | 48 | 92 → **100%** | 54 → **100%** | 2.8 → **4.1** |
| 사과 | 14 | 100% | 21 → **100%** | 2.7 → **4.5** |
| 배 | 15 | 100% | 100% | 2.9 |
| 상추 | 37 | 100% | 100% | 3.1 |
| 오이 | 36 | 100% | 100% | 3.1 |

### 21.1 추가한 블록

출처는 모두 `data/crops_for_llm.json`(농업기술길잡이 6권 사과 / 031 감자).

- **감자**(`blocks` 배열이 없어 새로 만들었다) — 밭 준비·심는 방법 / 거름 주기 /
  제초·멀칭 구멍 / 배토(북주기) 깊이 / 관수·배수로 / 병해충 / 시설 보온·환기 /
  건조제 살포
- **사과**(9블록) — 겨울 전정·열매가지 갱신 / 언피해·늦서리 / 묘목 심기·재식거리 /
  수분수·인공수분 / 열매솎기 / 병해충 / 초생 관리 / 고두병 예방 / 수확과 저장

`fertilizer` 블록이 있는데도 '밑거름 시비'에 수치가 안 붙던 이유를 찾았다 —
`guideLinesFor()`가 `b.directives || b.items` 만 읽는데 `fertilizer` 는 `rows`(표)만
갖고 있다. 그래서 감자에 '거름 주기' 블록을 `items` 로 따로 만들었다.

### 21.2 수행 방법이 없던 작업 4개

감자 '관수'·'시설보온'·'시설환기'·'건조제 살포'는 매칭되는 `TASK_HOWTO` 가 없어
'어떻게 하나요?' 버튼 자체가 안 붙었다('물주기'와 '관수'는 다른 낱말이다). 4개를 넣었다.

### 21.3 match 순서로 기존 매칭을 가로챈 버그 (수정)

새 항목을 `match: ['시설보온', '보온 관리']` 로 넣었더니 **오이의 평균 수치 줄이
3.1 → 2.9 로 떨어졌다.** 아래에 이미 오이용 `{ match: ['보온 관리'],
find: ['보온','지온을'] }` 가 있는데, `match` 는 위에서부터 먼저 걸리므로 내 항목이
오이의 '보온 관리'를 낚아채 `지온` 줄을 잃은 것이다. `match: ['시설보온']` 으로 좁혀
오이를 3.1 로 되돌렸다.

> 교훈: `TASK_HOWTO` 에 항목을 추가할 때는 **다른 작물의 기존 매칭을 가로채지 않는지**
> 확인해야 한다. 파일 상단 주석의 "좁은 뜻을 위에 둔다"가 이 규칙이고, 넓은 낱말을
> 위에 넣으면 아래 항목이 죽는다. 커버리지 %만 보면 100%라 드러나지 않았고 **평균 수치
> 줄 수**를 함께 재서 발견했다.

### 21.4 검증

- 5작물 × 전 작형 전 단계: 수행방법 100% · 세부 수치 100% · 누락 0종
- 브라우저(Playwright): 감자·사과 모두 '어떻게 하나요?'를 펼쳐 '○○ 기준 세부 수치'가
  렌더되는 것을 확인. 사과 `초생 관리`에 톨페스큐 10a당 2~7kg·10~11월 파종,
  `고두병 예방`에 소석회 200~300kg·염화칼슘 0.3~0.5%가 표시됐다.
- JS 문법 OK · sc-if 112/112 · sc-for 34/34

---

## 22. 과수(사과·배) 품종 기후 점수 모델 (2026-08-05)

### 22.1 먼저 측정했다 — 작물마다 가능 여부가 갈린다

품종을 기후 점수로 **순위 매기려면 품종별로 다른 기후 수치**가 있어야 한다. 실측:

| 작물 | 품종별로 다른 값 | 결론 |
|---|---|---|
| 감자 | 생육일수 80/90/90·110 + 비대적온 + 고온임계 | ✅ 순위 성립(기존 `cultivar_fit`) |
| 사과 | 적온 전부 18~24(공통값), **수확기만 다름** | ⚠️ 수확기 축으로 성립 |
| 배 | 적온 자체가 없음, **수확기만 다름** | ⚠️ 수확기 축으로 성립 |
| 상추 | 청치마·적축면이 생육일수(45~65)·적온(15~20)·고온플래그까지 **완전 동일** | ❌ 동점만 나옴 |
| 오이 | 3품종군 **완전 동일**(적온 20~25, 생육일수 없음) | ❌ 동점만 나옴 |

상추·오이는 무엇을 넣어도 동점이라 순위에 이유를 붙일 수 없다. 조건 매칭
(`cultivar_conditions`)에 그대로 뒀다.

### 22.2 사과·배 — 파종일 대신 수확기를 앵커로

`backend/scoring/cultivar_fruit_fit.py`(신규). 품종 자료가 확실히 가진 것은
`maturity.harvest_period`(후지 '10월 하순~11월 상순', 홍로 '9월 상·중순')다. 그 구간과
직전 30일(착색기)의 **평년 기상**으로 채점한다. 품종마다 수확기가 다르므로 같은 지역에서도
점수가 갈린다.

| 항목 | 가중치 | 근거 |
|---|---|---|
| 착색기주간 | 30 | `coloring_daily_mean_c` 12~13℃ |
| 착색기야간 | 15 | `coloring_night_mean_c` 8℃ |
| 수확기강수 | 20 | 하루평균 3mm 기준 + 집중강수일(50mm) 감점 |
| 수확기고온 | 20 | 최고 30℃ 초과일수 |
| 서리여유 | 10 | 수확 종료 vs 첫서리(게이트가 아니라 감점) |
| 토양 | 5 | 흙토람 pH |

배는 착색 기준값이 자료에 없어 두 항목을 빼고 재정규화한다(§10 원칙 — 없는 값에 100점을
주지 않는다). 착색 두 항목은 적온보다 **높을 때만** 깎는다 — 착색은 서늘해야 잘 되므로
낮은 것은 흠이 아니고, 아래로도 깎으면 서늘한 산지가 부당하게 손해를 본다.

결과(청송/나주): 후지 79.2/65.2 · 시나노골드 66.1/64.0 · 감홍 58.5/58.4 ·
홍로 46.0/43.2 · 쓰가루 40.6/36.0. 배(나주): 신고 89.3 · 신화 82.4 · 원황 81.9.
지역이 바뀌면 점수가 바뀐다(후지 착색주간 청송 67.9 → 나주 20.0).

### 22.3 구현 중 잡은 결함 4개

1. **`mmdd_diff(a,b)`는 `a-b`인데 인자를 뒤집어 썼다.** `window_days`가 음수→1로 클램프돼
   수확기 강수 하루평균이 구간 길이만큼 과대해졌다(17mm 구간이 16.5mm/일).
2. **같은 실수로 서리 여유 부호가 반대였다.** 수확 종료(11/10)가 첫서리(10/20)보다 21일
   늦은 후지가 '여유 21일'로 100점을 받았다.
3. **'성숙기온도' 축이 틀렸다.** 작물표준 `maturation_optimal` 20~25℃는 여름~초가을 과실이
   커지는 시기 기준인데 수확 구간(10월 하순, 9.2℃)에 대서 30점 바닥이 났다. 같은 자료가
   착색 적온을 12~13℃로 주고 있어 서로 모순이다. 걷어내고 착색 야간(8℃)으로 바꿨다.
4. **`_early_market_preferred`가 사과를 못 봤다.** `primary_use`만 읽는데 사과는
   `market_use`를 쓴다 — 쓰가루('여름 조기 출하')가 False로 잡혔다.

### 22.4 밝혀 둔 한계 — 조생종이 구조적으로 낮다

착색 적온 12~13℃는 사실상 **만생종 기준**이다. 자료에 숙기별 착색 기준이 없어 조생종은
착색기가 7~8월(25℃+)이라 바닥 점수를 받는다(쓰가루 40.6). 없는 숙기별 기준을 만들어
점수를 보정하지 않고, 대신 사실을 밝힌다.

- 조기 출하용 품종에 `조기 출하용` 배지 + "이 점수는 착색을 크게 보는데 착색 적온은
  만생종 기준이라 조기 출하용 품종은 낮게 나온다. 이 품종의 목적은 색보다 이른 출하다"
- 작물 주의문에도 같은 취지를 넣어 점수만 보고 배제하지 않게 했다

### 22.5 검증

| 항목 | 결과 |
|---|---|
| 감자 채점 회귀 | 파종·수확일·작형·항목점수 **불변** |
| 사과 5품종·배 3품종 점수 | **동점 0건** (순위에 이유가 붙는다) |
| 지역 변경 시 점수 변화 | 후지 청송 79.2 → 나주 65.2 |
| 5작물 응답 계약(필수 10필드) | 위반 0건 |
| 기상 원천 | ASOS 최근 10개 **완결 연도** 평년 — 올해·예보 미사용 |

## 23. 상추·오이 작형(재배 시기) 순위 모델 (2026-08-05)

§22.1에서 상추·오이는 "품종별로 다른 기후 수치가 없어 무엇을 넣어도 동점"이라 결론지었다.
이 절은 그 결론을 뒤집지 않고, **줄 세우는 대상을 품종에서 작형으로 바꿔** 순위를 만든다.
작형은 파종기가 두 달씩 다르므로 작기 기상이 실제로 갈린다.

| 지역 | 상추 작기 평균기온(작형별) |
|---|---|
| 정선(평창) | 고랭지 18.8℃ · 여름 18.9℃ · 가을 21.4℃ · 시설봄 8.3℃ · 겨울 4.0℃ |
| 광주(나주) | 여름 23.6℃ · 가을 22.1℃ · 시설봄 12.7℃ · 겨울 9.0℃ |

`backend/scoring/cultivar_season_fit.py`(신규) · `cultivar_data.SCORING_SEASON`.

### 23.1 작기 창은 '한 포기의 작기'다

작형 사양은 `chat_schedule.CROP_SCHEDULE[작물].seasons[].period` 문구가 원본이고
(`cultivar_conditions.parse_period`로 읽는다), 생육일수는 **(수확기 시작 − 파종기 시작)**이다.
파종일은 감자와 같이 파종기를 5일 간격으로 훑어 가장 좋은 날을 고른다.

> ⚠️ 처음에는 창을 파종기 시작~수확기 끝(151~212일)으로 잡았다. 그건 한 포기가 사는
> 기간이 아니라 그 작형에서 가능한 **모든** 파종·수확을 뭉갠 구간이어서, 상추 1월 파종
> 창의 평균기온이 1월과 5월의 혼합(5.9℃)으로 나왔다.

`season_window.window_metrics`를 쓰지 않고 `cycle_metrics`를 새로 뒀다 — 그 함수는 수확일이
12/31을 넘으면 12월 31일로 잘라서, 오이 촉성재배(10월 파종~이듬해 4월 수확)와 상추
겨울재배가 작기의 절반만 채점된다.

### 23.2 채점 축

| 상추 | 가중치 | 근거 |
|---|---|---|
| 생육적온 | 35 | 품종자료 `recommended_environment.growth_temperature_c` 15~20℃ |
| 추대위험 | 25 | 작물표준 `bolting_risk` 15/20/25℃ 눈금 |
| 서리저온 | 20 | 작기 서리일수(일 최저 0℃ 이하) |
| 작기강수 | 20 | `reference_data.PRECIP_THRESHOLDS` + 집중강수일 (`cultivar_fit.score_rain` 재사용) |

| 오이 | 가중치 | 근거 |
|---|---|---|
| 생육적온 | 30 | 품종자료 `growth_temperature_c` 20~25℃ |
| 주간적온 | 20 | 작물표준 `growing_optimal_day` 22~28℃ (작기 평균 최고기온) |
| 야간적온 | 15 | 작물표준 `growing_optimal_night` 15~18℃ (작기 평균 최저기온, 허용폭 1.5배) |
| 고온장해 | 15 | 품종자료 `growth_stress_temperature_c` high 30 / high_limit 35 |
| 저온장해 | 10 | 같은 자료 low 15℃ 미만 일수 |
| 작기강수 | 10 | 상추와 같음 |

일교차(7~10℃) 축은 넣지 않았다 — 노지 일교차는 지역차는 있지만 **작형차가 거의 없어**
판별력이 없다(정선 11.5~14.1 / 광주 8.6~10.5).

### 23.3 추대 위험을 '누적'으로 만들지 않은 이유

작물표준의 `bolting_risk`는 "25℃에서 파종 10일 만에 추대, 20℃ 20일, 15℃ 30일, 15℃
이하에서는 추대 크게 지연"이라는 **정성 서술에 붙은 예시 수치**다. 이것을 일별 진행률
(1/소요일수)로 누적하는 모델을 만들어 재봤더니 노지 3작형이 전부 **2.6~3.0**(= 한 작기에
추대가 세 번)으로 나왔다. 노지 여름·가을 상추는 실재하는 관행 재배이므로 그 결론은 틀렸다.
원문이 지지하는 것은 온도가 높을수록 추대가 빨라진다는 단조 관계와 세 개의 눈금뿐이라,
그 눈금 위에서만 점수를 매긴다(≤15℃ 100 / 20℃ 70 / 25℃ 25 / ≥30℃ 0).

### 23.4 시설 작형을 노지 기상으로 채점하는 것의 의미

상추 시설 봄재배·겨울재배, 오이 촉성·반촉성재배는 하우스 안에서 키운다. 하우스 **안**
기온은 알 수 없다. 그래서 점수를 '재배 적합도'가 아니라 **`노지 기상 적합도`**로 정의하고,
그 정의를 화면 문구(`score_note`)에 그대로 쓴다. 같은 숫자를 두 가지로 읽는다.

- 노지 작형 → 그것이 곧 재배 조건
- 시설 작형 → 시설이 난방·보온으로 메워야 하는 몫. **난방도일**(일평균기온이 적온 하한
  미만인 만큼의 누적)을 함께 계산해 "0점처럼 보이는 것"을 실제 부담 수치로 바꾼다
  (정선 오이 촉성재배 1,578℃·일)
- **강수 축은 시설 작형에서 제외하고 재정규화**한다 — 하우스 안에는 비가 오지 않는다

### 23.5 작형별 품종 제안

오이는 품종 자료의 `selection_guide`가 조건→품종군을 명시하므로 그 조건 문구를 근거로 달아
잇는다(촉성→취청, 반촉성→다다기·취청, 조숙→다다기·가시, 억제→가시).
상추는 `selection_guide`가 비어 있어 **매핑하지 않고 그 사실을 밝힌다** — 세 품종 모두 쓸 수
있고, 자료는 "고온기에는 내서성·만추대성 품종을 고르라"고만 적었는데 **어느 품종이
만추대성인지는 자료에 없다**. 대신 품종 생육일수로 걸러 근거를 붙인다(가을재배 작기 52일 <
로메인 60~90일 → "생육기간이 작기보다 길어요").

### 23.6 지역 게이트

- **고랭지재배**: 감자와 같은 기준(표고 ≥400m 또는 고랭지 기후대)만 후보로 두고, 뺀 작형은
  이유와 함께 `skipped`로 밝힌다(천안 "이 지역 표고 82m").
- **'제주·남부 시설'(상추 겨울재배)**: '남부'의 경계를 정할 근거가 없으므로 지역으로 걸러
  내지 않는다. 자료 문구를 배지로 남기고, 경고는 **그 지역에서 계산된 값**이 나쁠 때만
  붙인다(생육적온 < 50점). 제주에서는 이 작형이 2위 93.0점으로 올라오고 경고가 붙지 않는다.

### 23.7 구현 중 잡은 결함 6개

1. **작기 창을 파종기~수확기 전체로 잡았다** → §23.1.
2. **추대 누적 모델이 "모든 노지 상추는 추대"라고 말했다** → §23.3.
3. **`_structure`가 '시설' 부분문자열만 봤다.** 오이 억제재배(`전국 평지·시설`)가 시설로
   분류돼 오이 노지 작형이 조숙재배 하나만 남고 순위가 성립하지 않았다. 둘 다 적힌 작형은
   `노지·시설 겸용`으로 뒀다.
4. **적온 점수가 floor에 붙어 동점이 났다.** '거리/span 선형 + floor 15점'이라 오이 겨울
   작형은 세 온도 축이 모두 바닥에 붙어 촉성·반촉성이 **모든 지역에서 똑같이 27.5점**이
   나왔다. 더 나쁜 것은 파종일 탐색까지 무력해져(전부 동점 → 첫 후보가 이김) 반촉성재배
   권장 파종일이 가장 추운 12월 1일로 찍힌 것이다. 멀리서도 기울기가 남는 거리-구간 곡선
   (0→100, 2→85, 4→65, 7→40, 11→20, 16→5, 22→0)으로 바꿨다.
5. **억제재배에 '노지·터널조숙재배'라는 원문 문구를 잘못 인용했다.** 품종 매핑에서 억제재배도
   그 `selection_guide` 항목을 참고하는데(8월 파종이라 가시오이 근거가 성립), 문구를 그대로
   내보내니 자료가 억제재배를 터널조숙재배라 부른 것처럼 됐다. 작형 이름으로 한 번 더 확인.
6. **수확기 후기 저온 경고가 6월에도 떴다.** 기준을 생육 스트레스 하한(15℃)으로 둬서 나주
   12.4℃·제주 14.6℃의 6월 수확기에도 "보온이 필요합니다"가 붙었다. 자료가 따로 주는
   `growth_suppression_threshold`(10℃, "10~12℃ 이하에서 생육 크게 억제")로 바꿨다.

### 23.8 화면·챗봇 연결에서 잡은 결함 4개

1. **`cultivarAdjustFrom`이 `cultivar` 필드로만 찾았다.** 작형 응답에는 그 필드가 없어 항상
   못 찾고 "품종별 날짜 자료가 없어 일정은 작물 기준 그대로예요"가 찍혔다 — 작형의 권장
   파종일과 생육일수가 응답에 있는데도. `cultivation_type` 매칭을 더했다.
2. **`buildPlanFor`가 고른 작형을 무시했다.** 기후대 자동 선택만 하므로 "여름재배로 정하기"를
   눌러도 캘린더는 `🌱 시설 봄재배`로 만들어졌다. 게다가 화면 일정의 작형 이름에는 이모지가
   붙어 있고(`🌱 시설 봄재배`) 서버 이름은 순수 한글(`시설 봄재배`)이어서, 이름을 그대로
   비교하면 절대 맞지 않는다. 앞의 비한글을 떼고 비교한다.
3. **`score_note`의 마크다운이 별표로 그대로 찍혔다.** 템플릿은 문구를 글자로 렌더한다.
   페이로드 문구에서 `**`를 뺐다(전 작물 페이로드를 훑어 누출 0건 확인).
4. **챗봇이 작형을 품종으로 읽을 수 있었다.** `tool_get_cultivar_candidates`가 작형 줄을
   `품종` 키에 담으면 모델이 "여름재배 품종을 추천합니다"라고 답한다. `순위단위`를 싣고
   목록 키를 `작형`으로 갈랐으며, 품종은 각 작형의 `이작형에쓸품종`에서만 가져오도록
   SYSTEM_PROMPT에 규칙을 넣었다.

### 23.9 UI 단축 — 추천 근거 접기/펼치기

근거를 전부 펼쳐 두면 카드 하나가 15~20줄이고 작형이 5개면 화면이 근거로만 찬다.
머리줄(순위·이름·등급·점수·재배구조·파종기→수확)과 품종 버튼만 남기고, pros·cons·역병·
품종 설명·배지는 `❓ 추천 근거 보기 (N)` 버튼 안에 넣었다.

열림 여부는 **데이터로** 처리한다(닫히면 서버가 준 목록을 비운다). 템플릿에 `sc-if`를 한 겹
더 두면 `sc-for > sc-if > sc-for`가 되어 중첩이 깊어진다. 차단 사유(`blockers`)는 '심으면
실패하는' 정보라 접지 않고 항상 보인다.

측정(천안 상추 4작형): 접힘 **10줄/작형** → 펼침 21줄. 전체 41줄 → 52줄.
감자도 같은 토글이 붙는다(접힘 7줄 → 펼침 23줄).

### 23.10 검증

| 항목 | 결과 |
|---|---|
| 감자·사과·배 회귀 | 점수·순위 **불변** (추백 83.7 / 후지 79.2 / 신고 89.3) |
| 상추 5작형·오이 4작형 점수 | 4지역 **동점 0건** |
| 지역 변경 시 순위 변화 | 제주 상추 1위 **시설 봄재배**(94.0), 평창 1위 **여름재배**(94.2) |
| 페이로드 문장 결함(빈 값·마크다운) | 0건 |
| 브라우저 동작 | 접기/펼치기·품종 선택·작형별 일지 생성 정상, 페이지 오류 0건 |
| 챗봇 답변 | 작형을 품종이라 부르지 않음 · 시설 낮은 점수를 난방 부담으로 설명 |
| 기상 원천 | ASOS 최근 10개 **완결 연도** 평년 — 올해·예보 미사용 |
