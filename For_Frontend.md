# For_Frontend — 백엔드 병합 가이드

> 이 문서는 `For_Backend.md`(프론트 작성)에 대응하는 **백엔드 쪽 회신 문서**다.
> 프론트가 지켜야 할 게 없는지 걱정하지 않아도 되도록, **데이터 계약(§3)과 충돌 지점(§2)을
> 먼저 확인**하고 병합해달라.
> 작성 기준일: 2026-07-24 · 대상: `안농(安農)` 백엔드 → `m` 저장소 병합

---

## 0. 요약 (먼저 읽을 것)

1. `For_Backend.md`의 **§11 체크리스트 1번("작물 적합도 점수 API")**이 이번에 완성한 백엔드가 정확히 채우는 부분이다.
2. **환경변수 이름은 이미 일치한다** — `ASOS_DALY_SERVICE_KEY`, `SOIL_EXAM_STAT_SERVICE_KEY`를 백엔드도 똑같이 쓰고 있다. `.env` 파일 그대로 재사용하면 된다.
3. **다만 파일명 충돌이 최소 2건 있다** — `bjd_lookup.py`와 법정동코드 CSV. §2에서 바로 확인해달라. **코드를 합치기 전에 반드시 먼저 처리해야 하는 항목**이다.
4. 백엔드는 지금 **HTTP 서버로 감싸져 있지 않은 순수 Python 함수 모음**이다. `news_server.py`와 같은 방식(Python 표준 라이브러리 서버, 포트 8001)으로 감싸는 작업이 병합의 마지막 단계로 필요하다 — §4에 엔드포인트 스펙을 정의해뒀으니 그대로 얇은 래퍼만 씌우면 된다.

---

## 1. 백엔드가 제공하는 것 — 전체 구조

```
backend/
  scoring/
    reference_data.py           # 모든 근거값(근값/위험값/가중치)의 유일한 출처
    reading_guard.py            # 결측·이상치 방어 (재정규화 포함)
    scoring_engine.py           # 가중치×편차 점수화 (score_crop 핵심 함수)
    temperature_duration_rule.py # 사과·배 온도 정밀판정(실측 개화캘린더 기반)
    forecast_risk_signal.py     # 예보 기반 "연속위험일수+리스크등급"
    crop_station_registry.py    # 작물별 근거 관측소 목록
    test_*.py                   # pytest 67개, 전부 통과
  utils/
    region_mapper.py            # 지역명 -> 최근접 관측소(작물별)
    bjd_lookup.py                # ⚠️ 프론트에도 동명 파일 있음 - §2 참고
  api/
    asos.py                      # 기상청 ASOS 일자료 (강수·일조 season-to-date)
    soil.py                       # 흙토람 SoilExamStat V2 (pH·유기물·유효인산)
    weather.py                    # 기상청 단기예보 (온도, 시간단위)
  services/
    live_scoring.py              # 위 전부를 묶는 메인 함수: get_live_score(region, crop)
```

**대상 작물 5종**: 사과 · 배 · 오이 · 감자 · 상추 — `For_Backend.md`에 명시된 5종과 **정확히 일치**한다.

---

## 2. ⚠️ 병합 전에 반드시 먼저 처리해야 할 충돌 3건

### 충돌 A — `bjd_lookup.py` 파일명 중복

- 프론트: 프로젝트 루트에 `bjd_lookup.py` 존재(CSV 기반, CP949 인코딩)
- 백엔드: `backend/utils/bjd_lookup.py` 존재(독자적으로 개발됨, 인코딩 미확인 — 아래 조치 필요)

**해결 방법**: 백엔드 쪽 `bjd_lookup.py`를 버리고 **프론트 것을 그대로 재사용**하는 걸 권장한다. 이유:
- 프론트 문서에 CP949 인코딩 이슈가 이미 명시되어 있어 검증된 상태로 보임
- 두 구현을 유지하면 나중에 한쪽만 수정되는 사고가 날 수 있음(이번 프로젝트에서 실제로 이런 유형의 버그를 여러 번 겪었음)

**병합 시 할 일**:
```
1. backend/api/soil.py의 "from bjd_lookup import get_stdg_candidates" 부분을
   프론트 bjd_lookup.py가 제공하는 실제 함수명에 맞게 수정
2. 프론트 bjd_lookup.py에 get_stdg_candidates(sigungu_full_name) -> 
   {"exact": 법정동코드 or None, "children": [법정동코드, ...]} 형태의 함수가 없다면
   추가해줄 것 (구가 있는 시/군 지원 위해 하위 법정동코드 목록도 필요 - 천안시 사례처럼
   동남구/서북구 등 구 단위로만 조회되는 API 대응용)
3. 통합 후 soil.py가 정상 동작하는지 "천안시"로 재확인
```

### 충돌 B — 법정동코드 CSV 파일 중복

- 프론트: `국토교통부_법정동코드_20250805.csv` (프로젝트 루트, CP949)
- 백엔드: `data/raw/bjd_code.csv` (별도 경로로 업로드됨)

**해결 방법**: 같은 원본(국토교통부 법정동코드) 데이터일 가능성이 높다. **프론트 파일을 정본으로 채택**하고 백엔드 쪽 파일은 삭제 권장. `.env`의 `BJD_CODE_CSV` 키가 프론트 파일 경로를 가리키도록 통일.

### 충돌 C — `.env` 키 이름 재확인 (충돌은 아니지만 필수 확인)

프론트 문서(§8)에 이미 이렇게 정의되어 있다:
```
ASOS_DALY_SERVICE_KEY / ASOS_DALY_BASE_URL / ASOS_DALY_ENDPOINT
SOIL_EXAM_STAT_SERVICE_KEY / SOIL_EXAM_STAT_BASE_URL / SOIL_EXAM_STAT_OP_*
BJD_CODE_CSV
```
백엔드의 `asos.py`/`soil.py`는 `ASOS_DALY_SERVICE_KEY`, `SOIL_EXAM_STAT_SERVICE_KEY`만 직접 참조하고(`_BASE_URL`, `_ENDPOINT`, `_OP_*`는 코드 내부에 하드코딩되어 있음). 병합 시 다음 중 하나로 정리해달라:
- (권장) 백엔드 코드가 이미 URL을 알고 있으니, `.env`의 `_BASE_URL`/`_ENDPOINT`/`_OP_*` 키들은 프론트가 안 쓴다면 삭제
- 또는 백엔드 코드를 고쳐서 이 키들도 `.env`에서 읽어오게 통일 (필요하면 요청해달라, 바로 수정 가능)

---

## 3. 핵심 데이터 계약 — 작물 적합도 점수 API (신규)

`For_Backend.md` §6-A, §11 체크리스트 1번에 대응하는 신규 엔드포인트다. 아직 HTTP 서버로 감싸지 않았으니, **아래 스펙 그대로 `news_server.py`와 같은 방식으로 포트 8001에 추가**하면 된다.

### 요청

```
GET http://localhost:8001/api/crop-score/<작물명>?region=<지역명>
```
- `<작물명>`: `사과` `배` `오이` `감자` `상추` 중 하나 (URL 인코딩)
- `region`: 시군구명 문자열 (예: `천안시`, `평창군`, `논산시`). **법정동코드(10자리)가 아니라 지역명 문자열**을 받는다 — `For_Backend.md`에는 "법정동코드 → 실데이터"라고 되어 있는데, 백엔드 내부 로직(`region_mapper.py`)은 **지역명 문자열 입력**을 전제로 만들어졌다. 프론트가 이미 법정동코드를 들고 있다면(지도 선택 시 `code` 필드), 그 코드에 대응하는 지역명(`sigungu` 필드, `RegionMap.html`의 postMessage 계약에 이미 있음)을 같이 넘겨주면 된다. **법정동코드 자체는 지금 이 엔드포인트에서 안 써도 된다.**

### 응답 (200, 정상 매칭)

```json
{
  "status": "matched",
  "crop": "배",
  "region": "천안시",
  "score": 64.4,
  "grade": "normal",
  "grade_label": "양호",
  "breakdown": {
    "온도": { "value": "(시간단위 데이터)", "score": 61.1, "weight": 40 },
    "강수": { "value": 850.2, "score": 100, "weight": 22 },
    "일조": { "value": 1120.5, "score": 100, "weight": 18 },
    "pH": { "value": 6.34, "score": 100, "weight": 5 },
    "유기물": { "value": 17.82, "score": 40, "weight": 3 },
    "유효인산": { "value": 552.3, "score": 40, "weight": 4 },
    "EC": { "value": null, "score": null, "weight": 0 }
  },
  "risk_signals": {
    "온도": {
      "냉해": { "risk_grade": "낮음", "risky_days": 0, "message": "..." },
      "폭염": { "risk_grade": "낮음", "risky_days": 0, "message": "..." },
      "overall_risk_grade": "낮음"
    }
  },
  "reliability": "주의",
  "reliability_reason": "제외된 변수: EC(결측)",
  "matched_station": "천안",
  "distance_km": 13.24,
  "data_sources": {
    "온도": "기상청 단기예보 API",
    "강수": "ASOS 일자료 season-to-date (...)",
    "토양": "흙토람 SoilExamStat V2 (근사평균, EC 항목 없음-결측 고정)"
  }
}
```

### 응답 (실패)

지역을 못 찾거나(모호/미매칭) 존재하지 않는 작물명이면:
```json
{ "error": "지원하지 않는 작물명입니다: '딸기'" }
```
`For_Backend.md` §5의 다른 API들과 동일한 `{ "error": "..." }` 형식을 그대로 따른다.

### `grade`/`grade_label` — 프론트 `LEVEL_META`/`RANK`와 맞추기 위한 신규 필드

기존 `score_crop()`은 `total_score`(0~100 연속값)만 반환했는데, 프론트의 `CROP_ZONE`이 등급(good 등) 기반이라 **아래 매핑을 API 레이어에서 추가**했다:

| score 범위 | grade | grade_label |
|---|---|---|
| 80 이상 | `good` | 우수 |
| 60~79 | `normal` | 양호 |
| 40~59 | `caution` | 주의 |
| 40 미만 | `bad` | 위험 |

**이 구간·라벨은 프론트의 기존 `LEVEL_META`/`RANK` 정의에 맞춰 조정 가능하다** — 지금은 백엔드가 임의로 잡은 값이니, 프론트가 쓰던 정확한 값/문구가 있으면 알려달라. 그대로 맞춰서 고치겠다.

---

## 4. 병합 작업 순서 (제안)

```
1. §2의 충돌 3건 먼저 해소 (bjd_lookup.py 통합, CSV 통합, .env 키 정리)
2. backend/ 폴더를 m 저장소에 추가 (news_server.py와 나란히)
3. news_server.py와 같은 패턴으로 얇은 HTTP 래퍼 작성:
   - GET /api/crop-score/<crop>?region=<name> 을 받아서
     live_scoring.get_live_score(region, crop) 호출
   - 결과에 grade/grade_label 매핑 추가(§3 표 참고)
   - JSON 직렬화 시 datetime/date 객체가 섞여있을 수 있어(risk_signals 내부)
     기본 json.dumps로는 실패할 수 있음 - default=str 옵션 필요
   - CORS 헤더(Access-Control-Allow-Origin: *) 추가
   - 포트 8001 (news_server.py와 같은 서버 프로세스에 라우팅 추가하거나,
     별도 프로세스로 띄우고 start_servers.bat에 추가)
4. CropAdvisor.dc.html의 CROP_ZONE/CROP_BASE 하드코딩 부분을
   /api/crop-score/<crop>?region=... fetch 호출로 교체
5. 5개 작물 x 대표 지역 1곳씩 실제 브라우저에서 확인
```

## 5. 병합 후 확인해야 할 것 (체크리스트)

- [ ] `bjd_lookup.py` 하나로 통합, `soil.py`가 정상 동작
- [ ] 법정동코드 CSV 하나로 통합
- [ ] `.env` 키 정리 확인 (§2-C)
- [ ] 신규 엔드포인트 CORS 정상 작동 (브라우저 콘솔에서 CORS 에러 없는지)
- [ ] `grade`/`grade_label` 구간이 프론트 기존 등급 표시와 시각적으로 어울리는지
- [ ] 5개 작물 각각 최소 1개 지역으로 실제 화면에서 점수 표시 확인
- [ ] API 응답 지연(외부 API 3종 순차 호출이라 수 초 걸릴 수 있음) 발생 시 프론트에 로딩 상태 표시 필요 — 지금 백엔드엔 타임아웃만 있고(10초) 프론트 로딩 UX는 없음, 추가 필요 여부 확인
- [ ] `pytest backend/scoring/ -v` 67개 통과 유지 확인 (병합 중 파일 옮기다 깨지는 경우 방지)

---

## 6. 프론트가 알아야 할 백엔드의 한계 (투명하게 공유)

- **EC(전기전도도)는 흙토람 API 자체에 항목이 없어 항상 `null`이다.** 화면에 EC 관련 문구를 표시한다면 "측정 불가" 처리가 필요하다.
- **토양 pH·유기물·유효인산은 "근사 평균값"이다.** 흙토람이 구간별 면적 분포만 줘서, 원문 구간 경계값으로 가중평균한 근사치다(다만 원문 구간 확보로 신뢰도는 이미 상당히 높인 상태).
- **외부 API 실패 시에도 응답 자체는 항상 온다** — 실패한 변수만 결측 처리되고 나머지로 재정규화되어 `status: "matched"`가 나온다. `reliability` 필드(`정상`/`주의`/`신뢰불가`)로 데이터 품질을 구분해서 화면에 반영하면 좋다.
- **사과·배는 온도 판정이 매우 정밀하고(실측 개화캘린더), 오이·감자·상추는 상대적으로 덜 정밀하다**(연평균 기준). 다만 5개 작물 전부 "연속위험일수+리스크등급"(`risk_signals`)은 동일 수준으로 제공된다.
