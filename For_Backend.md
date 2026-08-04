# For_Backend — 백엔드 병합 가이드

> 이 문서는 **프론트엔드 작업물(`m` 저장소)** 에 향후 **백엔드**를 병합할 때 참고하기 위한 통합 명세다.
> Claude(또는 개발자)가 이 문서를 먼저 읽고, 아래 **데이터 계약**과 **병합 규칙**을 지키면서 백엔드를 붙이면 프론트를 깨뜨리지 않고 통합할 수 있다.
> 작성 기준일: 2026-07-24 · 서비스명: **안농(安農)** — 초보 귀농인 지역 맞춤 작물 추천 + 정책 추천.

---

## 0. 병합 시 Claude에게 (요약 지시)

1. **이 문서를 먼저 읽고** 프론트의 데이터 계약(§5)을 절대 임의로 바꾸지 말 것. 바꿔야 하면 프론트도 함께 수정.
2. 프론트는 **정적 HTML + 브라우저 JS**다. 백엔드는 **별도 프로세스**(현재 Python 표준 라이브러리 서버)로 두고, 프론트는 `fetch`로만 통신한다.
3. **비밀키는 서버 전용.** 클라이언트(HTML/JS)에 시크릿을 넣지 말고 백엔드 프록시를 경유한다(§7).
4. 지금 작물 점수는 **프론트 하드코딩(정적)** 이다(§6). 백엔드의 1순위 과제는 이를 **실데이터 API로 대체**하는 것.
5. Windows 환경. CSV 인코딩 주의(§8). 포트 8000(정적)·8001(API) 유지.

---

## 1. 프로젝트 개요 & 현재 상태

| 구분 | 상태 | 위치 |
|---|---|---|
| 웹 UI (탭형 SPA) | ✅ 완료 | `Beomin_web/CropAdvisor.dc.html` |
| 지역 선택 지도 | ✅ 완료 | `Beomin_web/RegionMap.html` (네이버 지도) |
| 농업 뉴스 API | ✅ 완료 (Python) | `Beomin_web/news_server.py` (포트 8001) |
| 정책 추천 (프로필) | ✅ 완료 (클라이언트 매칭) | `CropAdvisor.dc.html` + `policies.json` |
| 정책 데이터 빌드 | ✅ 완료 | `Beomin_web/build_policies.py` → `policies.json` |
| 법정동코드 조회 | ✅ 유틸 존재 | `bjd_lookup.py` (CSV 기반) |
| **작물 적합도 점수** | ⚠️ **정적(하드코딩)** — 백엔드로 대체 필요 | `CropAdvisor.dc.html` 내 상수 |
| **공공데이터 실연동**(기상·토양·작물적성) | ❌ 미구현 — 백엔드 과제 | — |

**대상 작물 5종**: 오이 · 배 · 사과 · 감자 · 상추.

---

## 2. 디렉터리 구조 (핵심만)

```
m/
├─ .env                       # 모든 API 키 (gitignore, 서버 전용) — §8
├─ For_Backend.md             # ← 이 문서
├─ PRD.md                     # 제품 요구사항
├─ bjd_lookup.py              # 이름→법정동코드(10자리) 조회 (CSV)
├─ 국토교통부_법정동코드_20250805.csv
└─ Beomin_web/
   ├─ CropAdvisor.dc.html     # 메인 SPA (프론트 전부)
   ├─ RegionMap.html          # 지역 선택 지도 (iframe)
   ├─ support.js              # DC 미니 프레임워크(React 기반) — 수정 금지
   ├─ region_tree.js          # 시도/시군구/동 트리 데이터
   ├─ image-slot.js           # 이미지 슬롯 유틸
   ├─ news_server.py          # 뉴스 API 서버 (8001)
   ├─ build_policies.py       # CSV → policies.json 변환기
   ├─ policies.json           # 정책 데이터(빌드 산출물, 프론트가 fetch)
   ├─ 귀농_농업_정책.final.csv  # 정책 원천 데이터(최종본, 970행)
   └─ start_servers.bat       # 두 서버 동시 실행
```

---

## 3. 실행 방법

```bat
:: 두 서버 동시 실행 (Windows)
Beomin_web\start_servers.bat
```
또는 수동:
```bash
# 1) 정적 웹 (프론트)
python -m http.server 8000 --directory Beomin_web
# 2) API 서버 (뉴스 등)
python Beomin_web/news_server.py         # 8001
```
- 접속: `http://localhost:8000/CropAdvisor.dc.html`
- 프론트는 API 서버를 **`http://localhost:8001`** 로 하드코딩 호출한다(§5-A). 백엔드 포트를 바꾸면 프론트도 함께 수정.

---

## 4. 프론트엔드 아키텍처 (간단)

- `support.js` 가 제공하는 **DC 프레임워크**(내부적으로 React) 사용. `<x-dc>` 템플릿 + `<script data-dc-script>` 의 `class Component extends DCLogic`.
- 템플릿 문법: `{{ 표현식 }}`, `<sc-if value="{{ bool }}">`, `<sc-for list="{{ arr }}" as="item">`. 이벤트: `onClick="{{ handler }}"`, `onChange="{{ handler }}"`.
- 상태: `this.state` + `this.setState(...)`. 화면 데이터는 `renderVals()` 가 반환하는 객체로 바인딩.
- 탭: `home / recommend / crops / guide / policy / favorites(=프로필) / news / detail`.
- **프로필 탭**(`favorites`)에 인적사항 폼 + 정책 자격 진단이 들어 있음.

> ⚠️ `support.js` 는 프레임워크 런타임이므로 **수정하지 말 것**.

---

## 5. 데이터 계약 (Data Contracts) — **백엔드가 지켜야 할 인터페이스**

### A. 뉴스 API (이미 구현됨 — 유지)
```
GET http://localhost:8001/api/news/<작물명>
예: /api/news/감자   (작물명 URL 인코딩)
```
- 응답(200): 기사 배열, 최대 6건
  ```json
  [{ "title": "고랭지 여름감자 …", "link": "https://…", "date": "2026-07-23" }]
  ```
- 오류: `{ "error": "메시지" }` (500/502)
- CORS: `Access-Control-Allow-Origin: *` 필요(정적 8000 → API 8001 호출).
- 키: `.env` 의 `NAVER_NEWS_CLIENT_ID/SECRET` 사용. 헤더 `X-Naver-Client-Id/Secret`.

### B. 정책 데이터 `policies.json` (프론트가 직접 fetch)
```
GET http://localhost:8000/policies.json     # 정적 파일
```
- 구조:
  ```json
  { "count": 907, "policies": [ { …정책… } ] }
  ```
- 정책 객체 스키마(필드 이름·타입 고정):
  ```json
  {
    "id": "문자열",
    "name": "정책명",
    "field": "서비스분야",
    "org": "소관기관명",
    "region": { "scope": "national|metro|local", "province": "충청북도", "city": "충주시" },
    "user": "개인|가구|…",
    "summary": "목적요약(≤220자)",
    "support": "지원내용(≤320자)",
    "criteria": "선정기준(≤700자)",
    "deadline": "신청기한",
    "method": "신청방법",
    "phone": "전화문의",
    "url": "상세조회URL",
    "ageMin": null, "ageMax": null,
    "tags": ["youth","senior","gwinong","woman","income","edu","land"],
    "kw": ["귀농","농업"]
  }
  ```
- **원천**: `귀농_농업_정책.final.csv` → `build_policies.py` 로 생성(§9). 정책 데이터가 바뀌면 CSV 갱신 후 재빌드.
- 백엔드가 정책 매칭을 서버화하려면 이 스키마를 그대로 반환하거나, `/api/policies/match` 엔드포인트로 §6-B 로직을 옮길 수 있다(프론트 fetch 대상만 교체).

### C. localStorage 스키마 (프론트 단독 저장 — 서버 미전송)
| 키 | 내용 | 형태 |
|---|---|---|
| `beomin_saved_regions` | 지도에서 저장한 귀농 지역 | `[{ code, name, province, sigungu, dong }]` |
| `beomin_personal_info` | **인적사항(정책 추천 입력)** | 아래 객체 |
| `gwinong_favorites` | 즐겨찾기한 작물 추천 | `[{ key, cropName, level, location }]` |

`beomin_personal_info` 필드(모두 문자열):
```
name, birth(YYYY-MM-DD), gender,
curResidence, targetRegion, job, cityYears,
farmCareer, landOwn, landArea, crop, eduDone,
targetType, householder, householdSize, income
```
> 개인정보는 **브라우저에만 저장**하고 서버로 보내지 않는 것이 현재 원칙. 백엔드가 계정/DB 저장을 도입하려면 이 원칙(동의·암호화·최소수집)을 먼저 설계할 것.

### D. 지도 → 앱 메시지 계약 (`RegionMap.html` → 부모창)
```js
window.parent.postMessage({
  type: 'selectRegion', id, code, name, province, sigungu, dong
}, '*')
```
- 부모(`CropAdvisor.dc.html`)는 `message` 이벤트로 수신 → `selectedRegion` 설정 + 프로필 지역 갱신.

---

## 6. 현재 "정적"인 로직 (백엔드로 대체 대상)

### A. 작물 적합도 점수 — **하드코딩** ⚠️
- `CropAdvisor.dc.html` 내 상수: `CROP_ZONE`(작물×기후존 등급), `CROP_BASE`(작물 기본정보), `LEVEL_META`, `RANK`, `ZONE_LABEL/ZONE_EXAMPLE`.
- 지역의 `zone`(기후존)에 따라 작물별 등급(good/…)을 **미리 정의한 표**로 보여줄 뿐, 실데이터 계산이 아님.
- **백엔드 과제**: 지역(법정동코드 10자리) → 실데이터 기반 점수:
  - 기상: 기상청 **ASOS 일자료**(15059093) — 최근접 관측소, 강수·기온.
  - 토양: 농진청 **농경지화학성 통계**(pH·유기물·유효인산·K 등).
  - 작물적성: 농진청 **작물별 토양적성 통계**.
  - 산출: 작물 5종 점수/등급 + 근거. (기존 UI가 소비하는 형태로 반환하도록 계약 정의 필요)

### B. 정책 매칭 — **클라이언트 JS** (동작 중)
- `CropAdvisor.dc.html` 의 `matchPolicies() / regionMatch() / canonProv() / userRegions() / ageFrom()`.
- 규칙 요약(백엔드로 이관 시 동일 로직 유지):
  - **지역**: 사용자 지역(저장지역 + 귀농예정지 + 현재거주지) vs 정책 `region`.
    `national`=전국 항상 후보 / `metro`=시도 일치 / `local`=시도+시군구 일치. 시도 약칭(충북↔충청북도) 정규화.
    사용자가 지역 미입력이면 지자체 정책은 "요건 확인". 다른 지역 전용이면 제외.
  - **나이/청년/고령**: `ageMin/ageMax`, `tags.youth/senior` 로 충돌 시 제외, 미상이면 "요건 확인".
  - **교육/농지/소득/귀농**: `tags.edu/land/income/gwinong` 이 있고 프로필이 충족 못하면 "요건 확인" 항목으로 표시.
  - **결과 2분류**: `✅ 해당 가능`(전부 표시) / `⚠️ 요건 확인 필요`(필요 요건 명시). 관련성 점수로 정렬.

---

## 7. API 키 & 보안 (필수)

- 유출 위험 있는 **모든 키·시크릿은 `.env`(gitignore)에만** 저장. 소스/문서/Git에 값 하드코딩 금지.
- **클라이언트 노출 금지**: 외부 API(공공데이터·네이버 뉴스/지오코딩)는 **백엔드 프록시**를 통해 호출.
  - 예외: 네이버 지도 **JS Client ID**만 클라이언트 사용 가능(단 콘솔에서 **도메인 제한** 필수). Secret은 서버 전용.
- 프론트→백엔드 호출은 CORS 허용 필요(현재 뉴스 서버가 `*` 허용).
- 키가 실수로 커밋되면 즉시 **재발급(rotate)**.

---

## 8. 환경변수 (`.env`) — 키 이름만 표기 (값은 파일 참조)

```dotenv
# 공공데이터포털 (data.go.kr) — 기상·토양 공용 서비스키
ASOS_DALY_SERVICE_KEY / ASOS_DALY_BASE_URL / ASOS_DALY_ENDPOINT
SOIL_EXAM_STAT_SERVICE_KEY / SOIL_EXAM_STAT_BASE_URL / SOIL_EXAM_STAT_OP_*

# 법정동코드 CSV 경로
BJD_CODE_CSV

# 네이버 지도 (Client ID는 클라, Secret은 서버)
NAVER_MAP_CLIENT_ID / NAVER_MAP_CLIENT_SECRET

# 네이버 뉴스검색 (서버 전용) — news_server.py 가 사용
NAVER_NEWS_CLIENT_ID / NAVER_NEWS_CLIENT_SECRET / NAVER_NEWS_ENDPOINT
```
- `.env` 는 **프로젝트 루트 1개**로 통합됨. `news_server.py` 는 자기 폴더→상위로 올라가며 루트 `.env` 를 자동 탐색한다.
- 백엔드 추가 키가 필요하면 여기에 이어서 정의하고, 이 표에 **이름만** 추가할 것.

**인코딩 주의(Windows)**
- 정책 CSV(`귀농_농업_정책.final.csv`): **UTF-8 with BOM**(`utf-8-sig`)로 읽고 쓴다.
- 법정동코드 CSV: **CP949**(`bjd_lookup.py` 참조).

---

## 9. 정책 데이터 빌드 파이프라인

```bash
# 원천 CSV 수정 후 재생성
python Beomin_web/build_policies.py    # 귀농_농업_정책.final.csv → policies.json
```
- `build_policies.py` 는 `귀농_농업_정책.final.csv` 를 우선 소스로 사용(없으면 `귀농_농업_정책.csv` 폴백).
- 필터: **개인/가구 대상**만 포함. 정책명에서 지역·나이·태그를 추출.
- 데이터 이력: 원본 1671행 → (축산 제거) → (비대상 작물 제거) → **최종 970행 / policies.json 907건**.
  - 백업: `Beomin_web`(또는 원본 폴더)의 `*.원본백업.csv`, `*.축산제거본.csv`.

---

## 10. 병합 규칙 (Do / Don't)

**Do**
- 백엔드는 새 프로세스/폴더(예: `backend/`)로 추가하고, 프론트와는 `fetch`(JSON)로만 연결.
- 기존 계약(§5)을 유지하거나, 바꿀 경우 프론트의 fetch 지점(`policies.json`, `http://localhost:8001/...`)을 함께 수정.
- 새 엔드포인트는 CORS 허용 + JSON 응답 + 오류 시 `{ "error": "..." }`.
- 비밀키는 `.env` 만. 외부 API는 프록시.

**Don't**
- `support.js` 등 프레임워크 런타임 수정 금지.
- 클라이언트 코드에 시크릿 하드코딩 금지.
- localStorage 키 이름/스키마 임의 변경 금지(§5-C).
- 개인정보를 동의·설계 없이 서버로 전송/저장 금지.

---

## 11. 백엔드 우선순위 체크리스트

- [ ] **작물 적합도 점수 API** (§6-A): 법정동코드 → 기상+토양+작물적성 → 5작물 점수. 프론트의 정적 `CROP_ZONE` 대체.
- [ ] 공공데이터 **프록시 엔드포인트**(키 서버 보관): ASOS 기상, 토양 화학성, 작물 적성.
- [ ] `bjd_lookup.py` 재사용: 지역명↔법정동코드(10자리, `STDG_CD`) 변환.
- [ ] (선택) 정책 매칭 서버화(§6-B) — 프론트 로직을 API로 이관.
- [ ] (선택) 사용자 계정·프로필 서버 저장 — **개인정보 보호 설계 선행**.
- [ ] 캐싱/재시도(공공데이터 장애 대비, PRD §11 참조).

---

### 참고 문서
- `PRD.md` — 제품 요구사항·API 목록·리스크.
- `API_KEYS.md` — 공공데이터 발급 체크리스트.
