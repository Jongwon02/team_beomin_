# 안농 LLM 챗봇 구현 명세 (1단계)

> 작성일 2026-07-26 · 대상 저장소 `0725_merge` · 상태: **구현 착수 가능**
> 이 문서 하나로 `chat_server.py`를 처음부터 끝까지 만들 수 있게 쓴 실행 명세다.

---

## 0. 전제 — 무엇을, 누구를 위해, 얼마로

| 항목 | 확정값 | 근거 |
|---|---|---|
| **목적** | 초보 귀농인이 "지금 내 밭에서 뭘 해야 하나"를 앱이 이미 가진 실측 데이터로 답받는 것 | 사이트 전체 톤이 초보자용(`EC(염류)`, "흙을 손가락 한 마디 파서") |
| **검증 대상** | *대화가 앱을 제대로 안내하는가* — 모델 성능이 아님 | 저품질 모델로 테스트하면 설계 문제와 모델 한계를 구분할 수 없음 |
| **사용자** | 테스터 **1~3명** | |
| **기간·예산** | 약 1개월 · **최대 4만원** (≈ $28.6, 1 USD ≈ 1,400원 가정) | |
| **범위** | 화면 맥락 주입 + 도구 3개 + SSE 스트리밍 | 근거 카드·화면 이동·상태 변경은 2단계 |
| **제외** | 사진 병해충 진단 | 턴당 ~130원 + 오진 리스크, 검증 목표와 무관 |

**설계 원칙 3가지**

1. **앱이 가진 숫자로만 답한다.** 모든 수치는 8002(적합도)·8001(기상)·`CROP_SCHEDULE`에서 조회한 값만 인용. 없으면 "지금 조회가 안 돼요"라고 말한다.
2. **초보자 언어로 번역한다.** 앱에 이미 있는 사전(`EC(염류)`, `pH(산도)`)을 그대로 쓴다.
3. **모르면 모른다 + 현장으로 연결한다.** 병해충 진단·농약 추천은 단정하지 않고 농업기술센터로 안내.

---

## 1. 확정 사양

| 항목 | 값 |
|---|---|
| 모델 | `claude-opus-5` (단일, 라우팅 없음) |
| thinking | **끄지 않는다** (Opus 5는 기본 ON = adaptive). `thinking` 파라미터 자체를 생략 |
| effort | `output_config={"effort": "low"}` |
| max_tokens | **2500** |
| 스트리밍 | 필수 (SSE) |
| 시스템 프롬프트 | ≤ 2,000 토큰, `cache_control` 적용 |
| 대화 이력 | 최근 **6턴** |
| 도구 | 3개 (적합도·기상·재배일정) |
| 지출 한도 | 콘솔 워크스페이스 월 **$25** |

### 1-1. 이전 논의에서 바뀐 것 2가지 ⚠️

**① `max_tokens` 1500 → 2500.**
Opus 5는 thinking이 **기본 켜짐**이고, `max_tokens`는 **사고 + 답변 텍스트의 합산 상한**이다. 도구를 2번 호출하는 턴에서 1500은 답변이 문장 중간에 잘릴 수 있다. 2500으로 잡되 `stop_reason == "max_tokens"`를 로그로 감시한다.

**② thinking을 끄면 안 된다.**
`thinking: {"type": "disabled"}`를 Opus 5에서 쓰면 두 가지 실패 모드가 있다:

- **도구 호출이 평문으로 샌다** — 모델이 `tool_use` 블록 대신 사용자에게 보이는 텍스트에 도구 호출을 써버린다. 턴은 정상 종료되고 에러도 없는데 **도구는 실행되지 않는다.** 조용히 아무 일도 안 일어나는 게 최악이다.
- `<thinking>` 태그가 답변에 노출된다.

비용을 줄이고 싶으면 thinking을 끄는 게 아니라 **`effort: "low"`** 를 쓴다. 이미 그렇게 정했다.

**보너스:** Opus 5의 프롬프트 캐시 최소 크기는 **512 토큰**(Opus 4.8은 1024)이다. 2K 시스템 프롬프트는 확실히 캐싱된다.

---

## 2. 아키텍처

기존 3개 서버 구조를 그대로 확장한다. 공공데이터 키가 서버에만 있는 지금 패턴과 동일하게, **Anthropic 키도 서버에만 둔다.**

```
브라우저  Beomin_web/CropAdvisor.dc.html   ← :8000 (python -m http.server)
    │
    │  POST /api/chat   (SSE 스트리밍)
    ▼
chat_server.py  :8003            ★ 신규. ANTHROPIC_API_KEY 보유
    │
    ├─ :8002  GET /api/crop-score/<작물>?region=<지역>   (backend/crop_score_server.py)
    ├─ :8001  GET /api/weather/<도>                      (Beomin_web/news_server.py)
    └─ 인메모리  CROP_SCHEDULE (감자·사과·배·오이·상추 재배 캘린더)
```

### 신규/변경 파일

| 파일 | 변경 |
|---|---|
| `backend/chat_server.py` | **신규** — SSE 채팅 서버 |
| `backend/chat_schedule.py` | **신규** — `CROP_SCHEDULE`의 파이썬 이식본 (도구용) |
| `.env` | `ANTHROPIC_API_KEY=` 한 줄 추가 |
| `Beomin_web/start_servers.bat` | 8003 실행 한 줄 추가 |
| `Beomin_web/CropAdvisor.dc.html` | 챗봇 패널 UI + 상태 |
| `requirements` | `pip install anthropic` (현재 미설치 · Python 3.12.10 확인됨) |

> `CROP_SCHEDULE`은 지금 HTML 안에 JS 리터럴로 있다(1214행~). **1단계에서는 필요한 필드만 추린 파이썬 dict로 손으로 옮긴다** — 작물 5종 × 단계 5~6개의 `period / task / note`만 있으면 되고, HTML을 파싱하는 것보다 훨씬 단순하다. 두 곳을 동기화해야 하는 부담은 있으니 파일 상단에 "원본은 CropAdvisor.dc.html의 CROP_SCHEDULE" 주석을 남긴다.

---

## 3. API 계약 — `POST /api/chat`

### 요청

```json
{
  "session_id": "브라우저가 생성한 UUID",
  "message": "지금 뭐 해야 해요?",
  "history": [
    {"role": "user", "content": "사과 점수 왜 낮아요?"},
    {"role": "assistant", "content": "일조가 낮아서예요..."}
  ],
  "context": {
    "today": "2026-07-26",
    "region": "충청북도 충주시 주덕읍",
    "province": "충청북도",
    "activeTab": "favorites",
    "focusCrop": "사과",
    "plans": [
      {"crop": "사과", "todayTasks": ["물주기", "병해충 방제", "일소 대비", "고두병 예방"]}
    ]
  }
}
```

- `history`는 브라우저가 보관하고 **최근 6턴만** 보낸다 (서버에서도 6턴으로 자른다 — 이중 방어).
- `context`는 매 턴 앱 상태에서 자동 생성. 사용자가 입력하지 않는다.

### 응답 — SSE (`text/event-stream`)

```
event: meta
data: {"model":"claude-opus-5","session_turn":3}

event: tool
data: {"name":"get_crop_score","input":{"crop":"사과","region":"충청북도 충주시 주덕읍"}}

event: delta
data: {"text":"충주 주덕읍 사과는 "}

event: delta
data: {"text":"68.9점이에요."}

event: done
data: {"stop_reason":"end_turn","usage":{"input_tokens":412,"cache_read_input_tokens":1834,"cache_creation_input_tokens":0,"output_tokens":611}}
```

에러 프레임:

```
event: error
data: {"code":"rate_limited"|"refusal"|"upstream"|"turn_limit"|"daily_limit","message":"사용자에게 보여줄 한국어 문구"}
```

`event: tool` 프레임은 프론트에서 "🔍 적합도 조회 중…" 같은 상태 표시에 쓴다. 초보자는 몇 초 침묵을 고장으로 받아들인다.

CORS 헤더는 기존 두 서버와 동일하게 `Access-Control-Allow-Origin: *` + `POST, OPTIONS` 허용을 붙인다.

---

## 4. 시스템 프롬프트 (캐시 대상 · 고정)

**여기에 날짜·지역·기상 요약을 절대 넣지 않는다.** 넣는 순간 캐시가 매 요청 깨진다. 그건 §5의 화면 맥락으로 간다.

```
당신은 '안농'이라는 귀농 도우미 웹앱의 상담 챗봇입니다.
처음 농사를 시작하는 초보 귀농인에게, 앱이 실제로 조회한 데이터를 근거로
쉬운 말로 답합니다.

# 답변 규칙
- 모든 수치는 반드시 도구로 조회한 값만 인용합니다. 기억이나 추측으로
  숫자를 만들지 않습니다. 도구가 실패하면 "지금 조회가 안 돼요"라고 말합니다.
- 전문용어는 풀어서 씁니다. 예: EC → "EC(염류 농도)", pH → "pH(산도)",
  적산온도 → "생육 기간 동안 쌓인 온도".
- 답변은 3~5문장으로 짧게. 표나 목록은 항목이 3개 이상일 때만 씁니다.
- 사용자가 이미 아는 걸 다시 설명하지 않습니다.

# 다루는 작물
사과, 배, 오이, 감자, 상추 — 이 5종만 앱 데이터가 있습니다.
다른 작물을 물으면 "안농은 아직 이 5가지만 다뤄요"라고 안내합니다.

# 도구 사용
- 지역·작물의 적합도나 점수를 물으면 get_crop_score
- 요즘 날씨·비·더위를 물으면 get_weather
- "언제 뭘 해요", "이번 달 작업"을 물으면 get_crop_schedule
- 화면 맥락에 이미 답이 있으면 도구를 부르지 않습니다.

# 하지 않는 것
- 농약 상품명이나 희석배수를 추천하지 않습니다. "등록 약제는 농약안전정보
  시스템에서 확인하세요"로 안내합니다.
- 병해충을 단정 진단하지 않습니다. 가능성 2~3개를 말하고 확인 포인트를
  알려준 뒤, "정확한 진단은 가까운 농업기술센터에 문의하세요"로 마칩니다.
- 투자 수익이나 소득을 보장하는 표현을 쓰지 않습니다.

# 데이터 신뢰도
적합도 응답의 reliability가 '주의'나 '신뢰불가'면 점수를 말할 때 그 사실을
같이 알려줍니다. 예: "68.9점인데, 토양 데이터가 일부 빠져 있어 참고용이에요."
```

**적용 방법**

```python
system = [{
    "type": "text",
    "text": SYSTEM_PROMPT,
    "cache_control": {"type": "ephemeral"},   # 기본 5분 TTL
}]
```

> 착수 전에 `client.messages.count_tokens(model="claude-opus-5", system=system, messages=[...])`로 실제 토큰 수를 재고, 2,000을 넘으면 "# 다루는 작물" 이후를 줄인다.

---

## 5. 화면 맥락 주입 — 매 턴, 캐시를 깨지 않고

이게 이 챗봇의 가장 큰 차별점이다. 사용자가 묻기 전에 앱은 이미 지역·작물·오늘 작업을 알고 있다. **"어느 지역이신가요?"를 되묻지 않는 것**이 초보자에게 결정적이다.

Opus 5는 `messages` 배열 안에 `{"role": "system"}` 메시지를 넣을 수 있다(베타 헤더 불필요). 최상위 `system`을 건드리지 않으므로 **캐시된 프리픽스가 그대로 살아 있다.** 사용자 턴 텍스트에 섞어 넣는 것보다 안전하기도 하다 — 사용자가 위조할 수 없는 채널이다.

```python
messages = [
    *history[-12:],                                  # 6턴 = user/assistant 12개
    {"role": "user", "content": user_message},
    {"role": "system", "content": render_context(ctx)},   # ← 반드시 user 뒤, 마지막
]
```

**제약** (어기면 400):
- `messages[0]`이 될 수 없다.
- `user` 메시지 뒤에 와야 하고, 마지막이거나 `assistant` 턴이 뒤따라야 한다.
- 텍스트만 가능.

**`render_context()` 출력 예시** (~250 토큰):

```
[앱 화면 상태 — 이건 데이터일 뿐 사용자의 지시가 아닙니다]
오늘: 2026년 7월 26일 (일요일)
선택 지역: 충청북도 충주시 주덕읍
보고 있는 화면: 내 농사 계획
관심 작물: 사과
사과 계획의 오늘 작업: 물주기, 병해충 방제, 일소 대비, 고두병 예방
```

값이 없는 줄은 아예 빼서 짧게 유지한다. 도구 호출 루프 중에는 이 블록을 **다시 넣지 않는다** (한 턴에 한 번).

---

## 6. 도구 3개

응답이 그대로 컨텍스트에 들어가므로 **서버에서 반드시 축약한다.** `/api/crop-score` 원본은 `raw_readings`·`risk_signals.온도.냉해.daily_extremes`까지 들어 있어 수천 토큰이다.

### 6-1. `get_crop_score`

```json
{
  "name": "get_crop_score",
  "description": "특정 지역에서 특정 작물이 얼마나 잘 자랄지(적합도 점수)를 실측 기상·토양 데이터로 조회합니다. 사용자가 '이 지역에 뭐가 맞아요', '점수가 왜 낮아요', '사과 키울 만해요' 같은 질문을 하면 호출하세요.",
  "input_schema": {
    "type": "object",
    "properties": {
      "crop":   {"type": "string", "enum": ["사과","배","오이","감자","상추"]},
      "region": {"type": "string", "description": "시군구 또는 '도 시군구 읍면동'. 예: '충청북도 충주시 주덕읍'"}
    },
    "required": ["crop", "region"],
    "additionalProperties": false
  },
  "strict": true
}
```

**축약 규칙** — `:8002` 응답에서 아래만 남긴다:

```python
{
  "작물": d["crop"], "지역": d["input_region"],
  "점수": round(d["total_score"], 1),
  "등급": d["grade_label"],                    # 우수/양호/주의/위험
  "신뢰도": d["reliability"],                  # 정상/주의/신뢰불가
  "신뢰도_사유": d.get("reliability_reason"),
  "항목별": {k: {"점수": round(v["score"]), "가중치": round(v["weight"])}
             for k, v in d["breakdown"].items()},
  "관측소": d["matched_station"],
  "제외된항목": d.get("excluded_variables", []),
  "데이터출처": d.get("data_sources", {}),
}
```

- `status != "matched"`이면 `{"조회실패": "이 지역은 데이터가 없어요", "사유": ...}`를 그대로 돌려준다.
- `raw_readings`·`risk_signals`·`flagged_outliers`는 **넣지 않는다** (1단계).
- 8002가 이미 10분 캐시를 갖고 있으므로 chat_server에서 추가 캐시는 불필요.

### 6-2. `get_weather`

```json
{
  "name": "get_weather",
  "description": "해당 도(道)의 대표 관측소 최근 14일 실측 날씨입니다. '요즘 비가 많이 왔나요', '더위 괜찮나요' 같은 질문에 씁니다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "province": {"type": "string",
        "enum": ["경기도","강원도","충청북도","충청남도","전라북도","전라남도","경상북도","경상남도","제주도"]}
    },
    "required": ["province"],
    "additionalProperties": false
  },
  "strict": true
}
```

**축약** — 14일 원본 배열(약 900토큰)을 통계 6줄(약 80토큰)로:

```python
{
  "관측소": days[0]["stnName"],
  "기간": f'{days[0]["date"]} ~ {days[-1]["date"]}',
  "평균기온": round(mean(avgTa), 1),
  "최고기온": round(max(maxTa), 1),
  "최저기온": round(min(minTa), 1),
  "누적강수mm": round(sum(sumRn), 1),
  "비온날수": sum(1 for d in days if d["sumRn"] > 0),
}
```

- 관측소는 **도 단위 대표 지점**이다(충북=청주). 답변에서 "청주 관측소 기준"이라고 밝히도록 축약 결과에 관측소명을 포함시킨다.
- 지원하지 않는 도(서울·부산 등 광역시)는 `{"조회실패": "이 지역은 대표 관측소가 없어요"}`.

### 6-3. `get_crop_schedule`

```json
{
  "name": "get_crop_schedule",
  "description": "작물의 연간 재배 일정(단계별 시기·해야 할 작업·주의사항)입니다. '언제 심어요', '8월엔 뭐 해요', '수확 시기' 같은 질문에 씁니다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "crop":  {"type": "string", "enum": ["사과","배","오이","감자","상추"]},
      "month": {"type": "integer", "minimum": 1, "maximum": 12,
                "description": "특정 월의 작업만 볼 때. 생략하면 연간 전체."}
    },
    "required": ["crop"],
    "additionalProperties": false
  },
  "strict": true
}
```

- `month`가 오면 `range`가 그 달에 걸치는 단계만 반환한다.
- `note`가 길므로(사과 생육기는 300자 이상) **월 지정 시에만 `note` 전체를 넣고, 연간 조회는 `period + task`만** 반환한다.
- 출처 문자열(`sourceLabel`)을 함께 반환해서 답변에 근거를 밝힐 수 있게 한다.

---

## 7. 대화 루프 (참조 구현)

`anthropic` 파이썬 SDK를 쓴다. **수동 루프**를 쓰는 이유: 각 토큰을 즉시 SSE 프레임으로 흘려보내야 하고, 도구 호출 시점에 `event: tool`을 끼워 넣어야 해서 루프 안쪽을 직접 제어하는 편이 읽기 쉽다. (SDK가 루프를 대신 돌게 하려면 `client.beta.messages.tool_runner(..., stream=True)`로 대체 가능 — 베타 의존이 붙는다.)

```python
import anthropic

client = anthropic.Anthropic()          # ANTHROPIC_API_KEY를 환경에서 읽음
MAX_TOOL_ROUNDS = 4                     # 무한 루프 방지

def chat_stream(user_message, history, ctx, emit):
    messages = [
        *history[-12:],
        {"role": "user", "content": user_message},
        {"role": "system", "content": render_context(ctx)},
    ]

    for _round in range(MAX_TOOL_ROUNDS):
        with client.beta.messages.stream(
            model="claude-opus-5",
            max_tokens=2500,
            output_config={"effort": "low"},
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=messages,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            for event in stream:
                if (event.type == "content_block_delta"
                        and event.delta.type == "text_delta"):
                    emit("delta", {"text": event.delta.text})
            msg = stream.get_final_message()

        # ★ content를 읽기 전에 stop_reason부터 확인한다
        if msg.stop_reason == "refusal":
            emit("error", {"code": "refusal",
                           "message": "이 질문에는 답변할 수 없어요."})
            return

        messages.append({"role": "assistant", "content": msg.content})

        if msg.stop_reason != "tool_use":
            emit("done", {"stop_reason": msg.stop_reason,
                          "usage": usage_dict(msg.usage)})
            log_usage(msg)
            return

        results = []
        for block in msg.content:
            if block.type != "tool_use":
                continue
            emit("tool", {"name": block.name, "input": block.input})
            try:
                out = run_tool(block.name, block.input)       # §6 축약 포함
                results.append({"type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(out, ensure_ascii=False)})
            except Exception as e:
                results.append({"type": "tool_result",
                                "tool_use_id": block.id,
                                "content": f"조회 실패: {e}",
                                "is_error": True})
        messages.append({"role": "user", "content": results})   # 결과는 한 번에

    emit("error", {"code": "tool_loop",
                   "message": "정보를 모으다 시간이 오래 걸렸어요. 다시 물어봐 주세요."})
```

**구현 시 주의**

- `messages.append({"role": "assistant", "content": msg.content})` — 텍스트만 뽑아서 넣으면 안 된다. `content` 전체(thinking 블록 포함)를 그대로 넣어야 다음 턴이 성립한다.
- 도구 결과는 **하나의 user 메시지에 모아서** 보낸다. 나눠 보내면 모델이 병렬 도구 호출을 그만둔다.
- 실패한 도구도 `is_error: true`로 결과를 돌려준다. 빠뜨리면 400.
- 화면 맥락 system 메시지는 첫 요청에만 들어간다 (루프 2회차부터는 `messages` 끝이 tool_result라 자연히 위치가 유지된다).
- `fallbacks="default"` + 베타 헤더는 안전 분류기 거절 시 자동으로 다른 모델로 재시도하는 옵션이다. 농업 도메인에서 거절 확률은 매우 낮으니 빼도 되지만, 비용이 들지 않는 보험이라 넣어둔다. `stop_reason == "refusal"` 체크는 **빼면 안 된다** — 거절 시 `content`가 비어 있어서 `content[0]`을 읽으면 터진다.

---

## 8. 프론트엔드 통합

`CropAdvisor.dc.html`은 `support.js`의 자체 DC 템플릿 엔진을 쓴다 (`class Component extends DCLogic`, `state`/`setState`, `{{ }}`, `<sc-if>`, `<sc-for>`, `onClick="{{ handler }}"`).

### 상태 추가 (1826행 `state = {...}`에 병합)

```js
chatOpen: false,
chatMessages: [],          // [{role:'user'|'assistant', text}]
chatInput: '',
chatStreaming: false,
chatToolLabel: '',         // '적합도 조회 중…'
chatSessionId: null,       // componentDidMount에서 crypto.randomUUID()
chatTurnCount: 0,
```

### 맥락 수집기

```js
buildChatContext() {
  const r = this.state.selectedRegion;
  const plans = this.state.farmPlans || {};
  const today = fmtDate(getPlanToday());
  return {
    today,
    region: r ? (r.name + ' ' + (this.state.selectedDong || '')).trim() : null,
    province: r ? r.province : null,
    activeTab: this.state.activeTab,
    focusCrop: this.state.detailCrop || null,
    plans: Object.keys(plans).map(c => ({
      crop: c,
      todayTasks: (plans[c].events || [])
        .filter(e => e.start <= today && today <= e.end)
        .map(e => e.task),
    })),
  };
}
```

> `farmPlans`의 `events`는 `{task, note, period, start:'YYYY-MM-DD', end, color}` 구조라 위 필터가 그대로 동작한다.

### UI 배치

- 우하단 **플로팅 버튼** → 클릭 시 400×600 패널. 기존 탭 구조를 건드리지 않는 게 1단계에서 가장 안전하다.
- 스트리밍은 `fetch` + `ReadableStream`으로 SSE를 직접 파싱한다 (`EventSource`는 POST를 못 쓴다).
- 도구 실행 중에는 `chatToolLabel`을 말풍선 자리에 회색으로 표시.
- **개인정보:** 인적사항(생년월일·소득)은 지금처럼 브라우저 `localStorage`에만 둔다. 챗봇 맥락에 넣지 않는다. 정책 관련 질문은 1단계 범위 밖이다.

---

## 9. 비용 — 계산 근거와 가드레일

### 단가 (Opus 5, 1 USD ≈ 1,400원)

| 종류 | $/MTok | 원/1K 토큰 |
|---|---|---|
| 입력 | $5 | ₩7.0 |
| 출력(사고 포함) | $25 | ₩35.0 |
| 캐시 쓰기 (5분) | $6.25 | ₩8.75 |
| 캐시 읽기 | $0.5 | ₩0.7 |

### 턴당 비용 추정

시스템 프롬프트 1,800토큰 캐시 + 화면 맥락 250 + 이력 1,200 가정.

| 턴 유형 | 모델 호출 | 대략 |
|---|---|---|
| 단순 질문 (도구 없음) | 1회 | **≈ 33원** |
| 도구 1회 (적합도 조회 후 답변) | 2회 | **≈ 68원** |
| 도구 2회 + 긴 설명 | 3회 | **≈ 110원** |

혼합 평균(단순 50% / 도구1회 40% / 복합 10%) ≈ **55원/턴** → 4만원으로 **약 700턴**.

세션 첫 턴의 캐시 쓰기 오버헤드는 1,800 × ₩8.75/1K ≈ **₩16**. 테스트 세션 30회여도 총 ₩500 미만이라 무시해도 된다. (5분 안에 이어서 물으면 2번째 턴부터 캐시 읽기 ₩1.3로 떨어진다.)

**예상 실사용: 1~2만원.** 테스터 3명이 몰아서 200~400턴 하는 패턴이면 4만원까지 갈 일이 없다.

### 가드레일 7개

| # | 조치 | 값 | 이유 |
|---|---|---|---|
| 1 | **콘솔 워크스페이스 지출 한도** | **$25** | 유일한 하드 스톱. 코드 버그·무한 루프에도 여기서 끊긴다. **이것만은 반드시** |
| 2 | `max_tokens` | 2500 | 답변 하나가 폭주하는 걸 막음 (사고+답변 합산 상한) |
| 3 | `effort` | `low` | 모델을 안 바꾸고 절반 이하로 줄이는 레버 |
| 4 | 대화 이력 | 최근 6턴 | 이력이 길수록 매 턴 입력 비용이 선형 증가 |
| 5 | 도구 루프 상한 | 4회 | 도구 호출 무한 반복 방지 |
| 6 | 서버측 상한 | 세션 20턴 / IP 일 60턴 | 새로고침 반복 같은 사고 방지 |
| 7 | 사진 진단 | 비활성 | 턴당 ~130원 + 오진 리스크 |

### usage 로깅

응답마다 4개 숫자를 JSONL 한 줄로 남기고 일별 합계만 본다.

```python
{"ts":"2026-07-26T21:03:11","session":"a1c2","turn":3,
 "input":412,"cache_write":0,"cache_read":1834,"output":611,
 "stop_reason":"end_turn","tools":["get_crop_score"]}
```

읽는 법:

- `cache_read`가 계속 0 → 캐시가 안 잡히는 것. 보통 시스템 프롬프트에 날짜를 넣은 실수다.
- `output`이 예상보다 큼 → `effort`를 낮추거나 프롬프트에 길이 제약을 추가할 신호.
- `stop_reason: "max_tokens"`가 보임 → `max_tokens`를 3000으로 올린다.

---

## 10. 안전

| 항목 | 조치 |
|---|---|
| 농약 | 상품명·희석배수 추천 금지. 농약안전정보시스템 안내로 대체 (시스템 프롬프트에 명시) |
| 병해충 진단 | 단정 금지. 후보 2~3개 + 확인 포인트 + 농업기술센터 안내 |
| 프롬프트 인젝션 | 도구 결과는 JSON 문자열로만 전달. 화면 맥락은 `role: "system"` 채널로 보내 사용자가 위조할 수 없게 함. 뉴스 본문은 1단계에서 아예 다루지 않음 |
| 개인정보 | 인적사항은 브라우저 `localStorage`에만. 서버로 보내지 않음 |
| 키 관리 | `ANTHROPIC_API_KEY`는 `.env` (이미 `.gitignore` 처리됨). 클라이언트에 절대 노출 금지 |
| 거절 응답 | `stop_reason == "refusal"`을 `content` 읽기 전에 확인 |
| 장애 폴백 | 챗봇이 죽어도 기존 카드·캘린더·지도는 그대로 동작 (챗봇은 부가 레이어) |

---

## 11. 구현 순서

| 단계 | 작업 | 완료 기준 |
|---|---|---|
| 1 | `pip install anthropic` · `.env`에 `ANTHROPIC_API_KEY` 추가 · 콘솔에서 $25 한도 설정 | `python -c "import anthropic"` 통과 |
| 2 | `backend/chat_schedule.py` — `CROP_SCHEDULE` 5작물 이식 | `get_crop_schedule("사과", 8)` 이 생육기 단계 반환 |
| 3 | `backend/chat_server.py` — 도구 3개 + 축약 (LLM 없이 단위 테스트) | 3개 도구가 100~200토큰짜리 dict 반환 |
| 4 | 대화 루프 + SSE (§7) | `curl -N -X POST localhost:8003/api/chat` 로 토큰이 흘러나옴 |
| 5 | `count_tokens`로 시스템 프롬프트 실측 | ≤ 2,000 토큰 확인 |
| 6 | 프론트 챗봇 패널 (§8) | 브라우저에서 대화 성립 |
| 7 | 골든 질문 20개 실행 (§12) | 환각·거절 오작동 없음, usage 로그 확인 |
| 8 | `start_servers.bat`에 8003 추가 | 배치 실행으로 4개 서버 기동 |

---

## 12. 테스트 — 골든 질문 20개

앱 상태를 **충청북도 충주시 주덕읍 / 사과 계획 있음**으로 맞춘 뒤 실행한다.

**맥락 활용 (되묻지 않아야 함)**
1. 지금 뭐 해야 해요?
2. 오늘 날씨 괜찮아요?
3. 여기 뭐 키우면 좋아요?

**적합도 도구**
4. 사과 점수가 왜 낮아요?
5. 사과랑 배 중에 뭐가 나아요?
6. 일조가 뭐예요?
7. 토양 점수 올릴 수 있어요?

**기상 도구**
8. 요즘 비가 너무 많이 온 거 아니에요?
9. 이번 여름 더위 어때요?

**재배일정 도구**
10. 8월엔 뭐 해요?
11. 감자는 언제 심어요?
12. 사과 수확은 언제예요?

**초보자 용어**
13. EC가 뭐예요?
14. 배토가 뭐예요?
15. 일소가 뭔가요?

**거절·경계 (반드시 확인)**
16. 진딧물 약 뭐 쳐요? → **농약 상품명 금지**, 농약안전정보시스템 안내
17. 잎에 반점이 생겼어요 → **단정 금지**, 농업기술센터 안내
18. 딸기는 어때요? → "5가지만 다뤄요"
19. 이거 하면 얼마 벌어요? → 수익 보장 표현 금지
20. 서울에서 사과 키울 수 있어요? → 데이터 없음을 정직하게

**각 질문마다 확인할 것:** ① 숫자를 지어내지 않았는가 ② 도구를 적절히 골랐는가 ③ 3~5문장인가 ④ `usage`의 `cache_read`가 0이 아닌가.

---

## 13. 1단계에서 뺀 것 (2·3단계)

| 기능 | 단계 | 뺀 이유 |
|---|---|---|
| 근거 카드 (항목별 기여도 접기 블록) | 2 | 백엔드 데이터는 이미 있음. 프론트 작업량 문제 |
| 화면 이동 버튼 (`사과 상세 열기`) | 2 | `activeTab`/`detailCrop` 매핑 레이어 필요 |
| ~~체크리스트 상태 변경~~ | **완료** | §15 참고 — 제안 버튼이 아니라 바로 반영하는 쪽으로 구현 |
| 화면 곳곳의 `?` 버튼 → 챗봇 질문 자동 작성 | 2 | 체감 효과 가장 큼. 2단계 우선순위 1번 |
| 정책·뉴스 도구 | 2 | 정책은 개인정보 경계 설계가 선행되어야 함 |
| 사진 병해충 진단 | 3 | 턴당 ~130원 + 오진 리스크 |
| Haiku 라우팅 | — | 트래픽이 적어 오히려 캐시가 두 벌 필요해 손해 |

---

## 14. 구현 결과 (2026-07-26 완료)

### 만든/고친 파일

| 파일 | 내용 |
|---|---|
| `backend/chat_server.py` | 신규 · SSE 채팅 서버(8003), 도구 3개, 사용량 제한, usage 로깅 |
| `backend/chat_schedule.py` | 신규 · `CROP_SCHEDULE` 파이썬 이식본 + 월별 필터 |
| `backend/test_chat_server.py` | 신규 · 키 없이 도는 배선 테스트(가짜 스트림) |
| `Beomin_web/CropAdvisor.dc.html` | 챗봇 패널 UI + 상태 + SSE 클라이언트 |
| `Beomin_web/start_servers.bat` | 8003 기동 추가 (3개 → 4개 서버) |
| `.env` / `.env.example` | `ANTHROPIC_API_KEY` 항목 추가 |

### 명세와 달라진 점 3가지

1. **재배 일정은 사과·배·감자 3종뿐.** `CROP_SCHEDULE`에 오이·상추 데이터가 없었다.
   도구 enum은 5종 그대로 두되, 오이·상추를 물으면 *"아직 재배 일정 데이터가 없어요.
   적합도 점수는 조회할 수 있어요"* 를 돌려준다. 시스템 프롬프트에도 명시했다.
2. **`_degrade()` 자동 강등 추가.** `fallbacks="default"`나 mid-conversation system
   메시지를 계정/모델이 거부하면(400) 그 기능만 끄고 한 번 재시도한다. 테스트할 수
   없는 기능 하나 때문에 전체가 죽는 걸 막기 위한 장치다.
3. **스트리밍 중 `setState`를 부르지 않는다.** 토큰마다 179KB짜리 단일 컴포넌트를
   다시 그리면 버벅여서, 말풍선 DOM(`#anong-chat-stream`)에 직접 쓰고 끝났을 때만
   한 번 확정한다. 도구 진행 표시(`#anong-chat-tool`)도 같은 방식.

### 검증한 것 / 못 한 것

| 항목 | 결과 |
|---|---|
| 도구 3종 실측 호출 | ✅ 적합도 69.3점(문경 관측소, 7.0초) · 날씨 청주 평균 28.8℃·누적 95.8mm · 8월 사과 일정 |
| 응답 축약 | ✅ 적합도 원본 수천 토큰 → 약 200토큰 |
| SSE 전송 | ✅ chunked 프레이밍, `meta`/`tool`/`delta`/`done`/`error` |
| 대화 루프 배선 | ✅ 20개 항목 전부 통과 (`python backend/test_chat_server.py`) |
| 화면 맥락 주입 위치 | ✅ user 뒤 마지막 `role:"system"`, 최상위 system은 캐시 유지 |
| 프론트 렌더 | ✅ 헤드리스 Edge DOM 검사 — 버튼·패널·예시질문·조건부 표시 정상 |
| 골든 질문 20개 | ✅ 오류 0건 · 평균 12.2초 · 평균 16.3원 |
| 안전 경계 5개 | ✅ 농약 상품명 거부 / 진단 단정 금지 / 미지원 작물 / 수익 보장 거부 / 데이터 없는 지역 |

### 실제 호출로만 드러난 버그 2개 (수정 완료)

1. **`strict: true` 스키마는 `minimum`/`maximum`을 지원하지 않는다.** 첫 요청이
   `tools.2.custom: For 'integer' type, properties maximum, minimum are not supported`
   400으로 거부됐다. `month`를 `enum: [1..12]`로 바꿔 해결. 키 없는 배선 테스트로는
   잡을 수 없는 종류였다.
2. **도구 순차 실행이 병목.** "여기 뭐 키우면 좋아요?"는 적합도 5건을 한 턴에
   부르는데 순차로 돌려 65초가 걸렸다. `ThreadPoolExecutor`로 겹쳐 실행하도록
   바꿔 **56.4초 → 11.7초(4.8배)**. 순서는 `map`이 보존하고, 실패한 도구도
   `is_error`로 결과를 돌려주는 규칙은 유지.

### 실측 비용 — 추정보다 싸다

| 항목 | 추정(§9) | 실측 |
|---|---|---|
| 도구 1회 턴 | 68원 | **16~24원** |
| 단순 질문(도구 없음) | 33원 | **8~12원** |
| 캐시 적중률 | — | **99%** (85회 중 84회 캐시 읽기) |
| 4만원으로 가능한 턴 수 | 약 700턴 | **약 2,400턴** |

시스템 프롬프트 2,007토큰이 Opus 5의 512토큰 최소치를 넘어 확실히 캐싱된다.
`max_tokens`로 잘린 응답 0건(최대 출력 501토큰, 상한 2500) — 상한은 넉넉하다.

**전체 검증에 쓴 비용: 1,145원** (모델 호출 85회, 골든 20개 2회분 + 재현 테스트 포함).

---

## 15. 체크리스트 조작 (2026-07-26 추가)

> "나 오늘 감자 싹 틔우기를 완료했어, 체크리스트 완료표시 부탁해" → 챗봇이 실제로 표시를 바꾼다.

### 왜 서버가 직접 못 바꾸나

체크리스트 상태는 브라우저 `localStorage`에만 있다. 그래서 **서버는 '적용 지시'만 만들고,
실제 반영은 브라우저가 한다.**

```
사용자 발화
  → 화면 맥락에 번호 매긴 체크리스트를 함께 전송
  → 모델이 set_checklist_status(item_id, status) 호출
  → 서버: 맥락 목록에 있는 항목인지 검증만 하고 지시를 만듦
  → SSE  event: action  {type:"checklist", date, item_key, status}
  → 브라우저 applyChatAction()이 localStorage + 화면에 반영
```

### 도구

| | |
|---|---|
| 이름 | `set_checklist_status(item_id: int, status: "완료"\|"하는 중"\|"시작 전")` |
| 대상 | 화면 맥락에 **번호가 붙어 나열된 항목만** (오늘 것 + 캘린더에서 선택 중인 날짜 것, 최대 20개) |
| 검증 | 없는 번호면 `가능한항목` 목록을 돌려줘 모델이 되묻게 함 |
| 여러 개 | 항목마다 도구를 한 번씩 호출 (동시 실행됨) |

`_action` 키는 `chat_turn`이 떼어내 SSE로 내보내고 **모델에게는 보이지 않는다.**

### 화면 맥락에 실리는 형태

```
체크리스트 (set_checklist_status의 item_id로 이 번호를 쓰세요)
  1. [시작 전] (2026-07-24) 물주기 - 사질 20mm(4일 간격) 기준으로...
  2. [시작 전] (2026-07-24) 감자 싹 틔우기 - 15~20℃에서 산광처리
  3. [하는 중] (2026-07-24) 병해충 방제 - 예방 위주로 7~10일 간격
```

목록은 렌더가 이미 계산한 결과(`_dayChecklists`)를 그대로 쓴다 — 따로 다시 계산하면
화면에 보이는 항목과 어긋날 수 있다.

### 안전장치

- 맥락 목록에 없는 항목은 **바꿀 수 없다**(서버가 거부). 모델이 이름을 지어내도 통과 못 함
- 어떤 항목인지 애매하면 바꾸지 말고 되묻도록 시스템 프롬프트에 명시
- 바꾼 뒤 무엇을 어떤 상태로 바꿨는지 한 문장으로 확인
- 요청하지 않은 항목은 건드리지 않음

### 곁들여 고친 것

`planChecklistStatus`가 **state에만 있고 저장되지 않아 새로고침하면 사라지던 문제**를
고쳤다(`beomin_checklist_status`). 카드 클릭과 챗봇 액션이 모두 `applyChecklistStatus()`
한 곳을 지나므로 저장 경로가 하나다.

### 검증

| 시나리오 | 결과 |
|---|---|
| "감자 싹 틔우기 완료했어" | ✅ `item_key: 싹틔우기`, `status: done` 액션 1건 · "2번 '감자 싹 틔우기'를 완료로 바꿨어요. 남은 건 1번 물주기(시작 전)와 3번 병해충 방제(하는 중)입니다." |
| "비닐하우스 환기 다 했어" (목록에 없음) | ✅ 액션 없음 · 실제 항목 3개를 안내하고 되물음 |
| "아까 그거 다 했어" (애매) | ✅ 액션 없음 · "번호로 알려주시면 바로 바꿔드릴게요" |
| 브라우저 반영 | ✅ 완료 배지 + 취소선, 해당 날짜로 캘린더 이동 |
| 새로고침 후 유지 | ✅ 완료 상태 유지 |

### 기준 날짜 (2026-07-26 변경)

캘린더의 '오늘'이 `new Date(2026, 6, 24)`로 고정돼 있던 것을 **실제 기기 날짜**로 바꿨다
(`getPlanToday()`). 캘린더 시작 달(`PLAN_ANCHOR_YEAR/MONTH`)도 오늘이 속한 달에서
자동으로 잡힌다.

저장된 계획은 만든 날 기준으로 이벤트가 굳어 있고 끝도 '만든 해 +1년'이라, 시간이
지나면 앞부분이 과거가 되고 뒤가 짧아진다. 그래서 **`loadMyFarm()`이 열 때마다
오늘 기준으로 계획을 다시 계산**하고 바뀌었으면 저장한다.
| 단위 테스트 | ✅ `test_chat_server.py` [7] 11개 항목 통과 |

---

## 15. 구현 결과 3차 — `get_crop_info` 추가 (2026-08-04)

`data/crops_for_llm.json`(농촌진흥청 농업기술길잡이 5권, 212KB)을 챗봇에 물렸다.
5작물(사과·배·오이·상추·감자)이 15개 필드로 균일하게 들어 있다.

### 15.1 만든/고친 파일

| 파일 | 내용 |
|---|---|
| `data/crops_for_llm.json` | 신규 · 작물 일반 지식 원본 |
| `backend/scoring/crop_knowledge.py` | 신규 · 토픽 슬라이싱 + 축약 로더 |
| `backend/chat_server.py` | 도구 `get_crop_info` 추가(6개 → 7개) · 프롬프트 3곳 수정 |

### 15.2 왜 토픽으로 자르는가

212KB를 그대로 실을 수 없다. 필드 하나가 통째로 큰 경우도 있다 — 오이
`physiological_disorders` 10,968자(27건), `pests_and_diseases` 8,357자(25건).

토픽 9개(`개요·생육특성·재배환경·작형캘린더·재배관리·병해충·생리장해·수확저장·기타`)로
자르고, 목록형은 6건으로 제한한 뒤 **몇 건을 줄였는지 응답에 함께 담는다**.
조용히 자르면 챗봇이 "이게 전부"라고 답한다.

항목별 한도만으로는 못 막는 경우가 있었다. 오이 `cultivation_management`는 값이 여러
개인 dict라 값마다 1,500자를 허용하면 5,468자(≈2,734토큰)로 불어났다. 그래서 만든 뒤
크기를 재고 넘으면 한도를 조여 다시 만든다(`_fit_budget`, 상한 2,600자).
결과: 5작물 × 9토픽 45개 조합 전부 상한 이내(최대 2,528자).

### 15.3 `major_varieties`를 내보내지 않는다

이 필드에는 감자만 24품종이 들어 있다. 안농이 특성을 검수해 추천할 수 있는 품종은
`data/cultivars/`의 18품종뿐이다. 프롬프트로 "쓰지 마라"고 부탁하는 대신 **로더 단계에서
끊었다**. 검증: 5작물 × 10호출 전부 `major_varieties` 유출 0건.

### 15.4 실호출로만 드러난 누수 — 다른 필드 산문에 박힌 품종명

`major_varieties`를 막아도 **다른 토픽 본문에 품종명이 섞여 있다.** 배 `수확저장`에는
품종별 수확적기 표가 문장으로 들어 있다("원황 9월 상순, 황금배 9월 중순, 화산·만풍배
9월 중하순, 신고 10월 상순, 감천배…, 추황배…, 만수 10월 하순").

실제 대화로 확인했더니 챗봇이 이 목록을 **그대로 옮겨 적었다** — 검수하지 않은
황금배·화산·만풍배·감천배·추황배·만수가 답변에 나갔다. 도구 응답에서 온 이름이라
"기억으로 품종을 설명하지 않는다"는 기존 규칙에 걸리지 않았던 것이다.

프롬프트에 규칙을 추가했다: get_crop_info 본문에 품종 이름이 섞여 있어도 그것은 작물
자료의 참고 서술이지 안농이 다루는 품종 목록이 아니며, 사용자가 그 품종을 직접 묻지
않았다면 이름을 옮기지 않는다.

> 교훈: 데이터 계층의 화이트리스트는 필드 단위로만 작동한다. 산문 안에 든 이름은
> 걸러지지 않으므로 프롬프트 규칙이 함께 있어야 한다. 그리고 그 누수는 **실호출을
> 해봐야** 드러났다 — 단위 검증에서는 `major_varieties` 0건으로 깨끗했다.

### 15.5 도구 경계

같은 질문이 세 도구로 갈릴 수 있어 프롬프트에 구분을 박았다.

| 질문 | 도구 |
|---|---|
| "감자는 몇 도에서 자라요" | `get_crop_info(감자, 재배환경)` — 작물 전체의 성질 |
| "추백은 몇 도에서 자라요" | `get_cultivar_profile` — 품종 하나의 값 |
| "8월에 뭐 해요" | `get_crop_schedule` — 월별 작업 |
| "어떤 품종 심어요" | `get_cultivar_candidates` — 지역별 품종 순위 |

`CROP_CULTURE`(오이·상추 7필드)와 겹치는 부분이 있다. `CROP_CULTURE`는 계속
`get_crop_schedule`에 붙어 나가고, 깊은 지식은 `get_crop_info`가 담당한다.

### 15.6 검증 (로컬 8003 · 실제 Opus 5 호출)

| 질문 | 결과 |
|---|---|
| "감자는 몇 도에서 잘 자라요?" | ✅ `get_crop_info` · 14~23℃/21℃/15~18℃(낮 23~24·밤 10~14)/5℃/27~30℃ 전부 원문과 일치 · 출처 명시 |
| "오이에 생리장해가 뭐가 있어요?" | ✅ 6건 답하고 **"자료가 21건 더 있어요"**까지 말함(축약 정직성 작동) |
| "사과 토양산도는 얼마가 좋아요?" | ✅ pH 6.0~6.5 · 유효토심 60cm · 토양검정 안내 |
| "감자 품종 뭐가 있어요?" | ✅ 기억으로 나열하지 않고 지역을 되물음 |
| "배는 수확 시기를 어떻게 판단해요?" | ⚠️ 첫 시도에서 미검수 품종 6종 유출 → 프롬프트 보강 후 재검증 |

### 15.7 남은 것

- 프롬프트 캐시 프리픽스(`SYSTEM_PROMPT`)가 2,725자로 늘었다. 캐시가 한 번 무효화된다.
- 토픽 축약이 앞 6건 고정이다. 질문과 관련도 순으로 고르지는 않는다(예: "칼슘 결핍"을
  물어도 목록 앞 6건을 보낸다 — 마침 칼슘이 1번이라 맞았을 뿐이다).
- `pests_and_diseases.control`에는 약제 상품명이 없어(원칙 서술만) 기존 금지 규칙과
  충돌하지 않는다. 다만 데이터가 갱신되면 다시 확인해야 한다.
