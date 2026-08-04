# DB.md — 안농(安農) 온라인 전환 설계서 (Supabase + Vercel)

로컬 4개 파이썬 서버 + 브라우저 localStorage로 돌아가는 현재 구조를,
**Vercel 배포 + Supabase DB**로 옮겨 실제 사용자가 인터넷에서 쓸 수 있게 만들기 위한 설계 문서.

- DB: **Supabase** (Postgres + Auth + Storage)
- 배포: **Vercel** (정적 프런트 + Python 서버리스 함수)
- 대상 코드: `Beomin_web/CropAdvisor.dc.html`, `Beomin_web/news_server.py`, `backend/crop_score_server.py`, `backend/chat_server.py`

---

## 1. 현재 데이터가 사는 곳 (전환 대상 인벤토리)

### 1.1 브라우저 localStorage — 사용자 데이터 (기기에 갇혀 있음)

| 키 | 상수 (위치) | 내용 | 형태 |
|---|---|---|---|
| `beomin_my_farm` | `MYFARM_KEY` (`CropAdvisor.dc.html:2114`) | 내 귀농지역 1곳 + 나의 작물 1개 + 농사 계획 | `{region:{province,sigungu,dong,code,fullName,zone}, crop, plan, createdAt}` |
| `beomin_checklist_status` | `CHECK_KEY` (:2116) | 체크리스트 진행 상태 | `{"작물\|YYYY-MM-DD\|항목키": "doing"\|"done"}` |
| `beomin_prep_suggestions` | `PREP_KEY` (:2119) | 7일 예보 준비할 일 상태 | `{"플랜key\|prep\|id": "dismissed"\|"todo"\|"doing"\|"done"}` |
| `beomin_region_log` | `REGION_LOG_KEY` (:2122) | 둘러본 지역 기록 (최근 8개, `REGION_LOG_MAX`) | `[{key,code,dong,name,province,sigungu,zone,dongs[]}]` |
| `beomin_personal_info` | `PI_KEY` (:2158) | 정책 수혜용 인적사항 16개 항목 | `PI_DEFAULTS` 참조 (이름/생년/소득 등 **민감정보**) |

> 폐기 키: `LEGACY_KEYS`(:2125) = `gwinong_favorites`, `beomin_saved_regions`, `beomin_farm_plans` — 첫 실행 때 삭제됨. DB로 옮기지 않음.

### 1.2 서버 인메모리 캐시 — 서버리스에서 **소멸**

| 서버 | 캐시 변수 | 키 | TTL |
|---|---|---|---|
| `news_server.py` | `_cache` | 작물명 | 1200초 (20분) |
| `news_server.py` | `_weather_cache` | 도(province) | 3시간 |
| `news_server.py` | `_weekly_cache` | 지역 full name | 3시간 (일부 누락 시 300초) |
| `crop_score_server.py` | `_cache` | (작물, 지역) | 600초 (10분) |
| `chat_server.py` | `_session_turns` | 세션 ID | 프로세스 생존 동안 (상한 20턴) |
| `chat_server.py` | `_ip_day` | (IP, 날짜) | 당일 (상한 60턴) |

### 1.3 디스크 쓰기 — Vercel 파일시스템은 **읽기 전용**

| 경로 | 쓰는 코드 | 용도 |
|---|---|---|
| `data/cache/ec_cache.json` | `backend/api/soil_ec.py:145-147` | 토양 EC 영속 캐시 |
| `data/chat_usage.jsonl` | `backend/chat_server.py:438-440` | 챗봇 토큰 사용량 로그 |

### 1.4 기준 데이터 (읽기 전용, 파일로 배포 중)

`policies.json`(783KB, `{count, policies}`) · `region_tree.js`(99KB) · `dong_coords.js`(128KB) · `crop_standards_v2.json` ·
`data/raw/hourly_temp_fruit_full.csv`(**11MB**) · `data/raw/bjd_code.csv`(3MB) · `data/raw/sigungu_coordinates.json`(1.3MB) ·
`data/raw/climate_clustering_final_v3.csv` · `apple/pear_bloom_dates.csv` · `data/processed/*.csv` · `region_cluster_map.json`

---

## 2. 온라인화하면 깨지는 것 → 대응

| 깨지는 지점 | 원인 | 대응 |
|---|---|---|
| 사용자 데이터가 기기에 갇힘 | localStorage | Supabase 테이블 + 익명 로그인 |
| 캐시가 매 요청 초기화 | 서버리스는 인스턴스가 매번 다름 | `api_cache` 테이블로 캐시 이전 |
| 사용량 제한 무력화 | 인메모리 dict | `rate_limits` 테이블 + 원자적 RPC |
| 디스크 쓰기 실패 | Vercel FS 읽기 전용 | `api_cache` / `chat_usage` 테이블 |
| 함수 번들 초과 위험 | `pandas` + CSV 11MB 동봉 | 기준 데이터를 DB 테이블로 이관 |
| CORS·하드코딩 주소 | `http://localhost:800x` 고정 | 동일 출처 상대경로 `/api/...` |
| 공공 API 키 노출 위험 | `.env` 22개 키 | Vercel 환경변수 (서버 함수 전용) |

---

## 3. 목표 아키텍처

```
브라우저 (Vercel 정적 호스팅)
  CropAdvisor.dc.html / support.js / region_tree.js / dong_coords.js
        │
        ├─ supabase-js ──────────────► Supabase Auth (익명 로그인)
        │                              Supabase Postgres (RLS로 본인 데이터만)
        │
        └─ fetch('/api/...') ───────► Vercel Python Functions
                                        ├─ /api/news/[crop]
                                        ├─ /api/weather/[province]
                                        ├─ /api/weekly/[region]
                                        ├─ /api/crop-score/[crop]
                                        ├─ /api/chat        (SSE 스트리밍)
                                        └─ /api/health
                                             │
                                   ┌─────────┴─────────┐
                        공공 API / Anthropic      Supabase (service_role)
                        (기상청·농진청·네이버)     api_cache · rate_limits · chat_usage
```

**핵심 원칙**
1. 사용자 데이터는 브라우저 → Supabase 직접 (RLS가 방어). 서버 함수 경유 불필요.
2. 외부 API 키·Anthropic 키는 **서버 함수만** 사용. 프런트에 절대 노출 금지.
3. `service_role` 키는 서버 함수 전용. 프런트는 `anon` 키만.

---

## 4. Supabase 스키마

### 4.1 인증 전략 — 익명 로그인부터

로그인 강제는 초보 귀농인 대상 서비스에서 이탈 요인이므로, **익명 로그인(Anonymous Sign-in)** 으로 시작한다.

- 첫 방문 시 `supabase.auth.signInAnonymously()` → `auth.users`에 행 생성, `user_id` 확보
- 기존 localStorage 데이터를 그 `user_id`로 1회 업로드 (§5)
- 나중에 이메일/카카오 연동 시 `linkIdentity()`로 같은 `user_id` 승계 → 데이터 유지

> Supabase 대시보드: Authentication → Sign In / Providers → **Anonymous sign-ins 활성화** 필요.

### 4.2 확장 · 공통

```sql
create extension if not exists pgcrypto;

-- updated_at 자동 갱신
-- search_path를 고정한다. 없으면 Supabase 보안 린터가 function_search_path_mutable 경고를 낸다.
create or replace function public.touch_updated_at()
returns trigger language plpgsql security invoker set search_path = public as $$
begin
  new.updated_at = now();
  return new;
end $$;
```

### 4.3 사용자 데이터 테이블

```sql
-- ── 프로필 ──────────────────────────────────────────────────────────────
create table public.profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  nickname      text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create trigger t_profiles_touch before update on public.profiles
  for each row execute function public.touch_updated_at();

-- auth.users 생성 시 프로필 자동 생성
create or replace function public.on_auth_user_created()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id) values (new.id) on conflict do nothing;
  return new;
end $$;
create trigger t_on_auth_user_created after insert on auth.users
  for each row execute function public.on_auth_user_created();


-- ── 나의 귀농지역 1곳 + 나의 작물 1개 (1인 1행) ─────────────────────────
-- localStorage: beomin_my_farm
-- plan(jsonb)은 buildPlanFor()가 '오늘' 기준으로 매번 재계산하므로 캐시 성격이다.
-- 저장해두면 오프라인/첫 렌더에 유리하지만, 없으면 프런트가 다시 만들 수 있다.
create table public.my_farm (
  user_id       uuid primary key references auth.users(id) on delete cascade,
  crop          text not null check (crop in ('사과','배','오이','감자','상추')),
  region_code   text not null,                    -- 법정동 코드
  province      text not null,
  sigungu       text not null default '',
  dong          text not null default '',
  full_name     text not null default '',
  zone          text,                             -- coolHighland / temperateInland / ...
  plan          jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create trigger t_my_farm_touch before update on public.my_farm
  for each row execute function public.touch_updated_at();


-- ── 체크리스트 진행 상태 ────────────────────────────────────────────────
-- localStorage: beomin_checklist_status  { "작물|YYYY-MM-DD|항목키": "doing"|"done" }
-- 항목 하나를 토글하는 것이 실제 사용 패턴이므로 행 단위로 둔다(부분 업데이트·동시성 유리).
create table public.checklist_status (
  user_id       uuid not null references auth.users(id) on delete cascade,
  item_key      text not null,
  status        text not null check (status in ('doing','done')),
  updated_at    timestamptz not null default now(),
  primary key (user_id, item_key)
);
create trigger t_checklist_touch before update on public.checklist_status
  for each row execute function public.touch_updated_at();


-- ── 7일 예보 준비할 일 제안 상태 ────────────────────────────────────────
-- localStorage: beomin_prep_suggestions  { "플랜key|prep|id": 상태 }
-- 키가 없으면 '아직 결정 안 한 새 제안'이라는 기존 의미를 그대로 유지한다.
create table public.prep_status (
  user_id       uuid not null references auth.users(id) on delete cascade,
  prep_key      text not null,
  status        text not null check (status in ('dismissed','todo','doing','done')),
  updated_at    timestamptz not null default now(),
  primary key (user_id, prep_key)
);
create trigger t_prep_touch before update on public.prep_status
  for each row execute function public.touch_updated_at();


-- ── 둘러본 지역 기록 (최근 8개) ─────────────────────────────────────────
-- localStorage: beomin_region_log (REGION_LOG_MAX = 8)
create table public.region_log (
  user_id       uuid not null references auth.users(id) on delete cascade,
  entry_key     text not null,                    -- '{code}|{dong}' (기존 key와 동일 규칙)
  region_code   text not null,
  dong          text not null default '',
  name          text not null default '',
  province      text not null default '',
  sigungu       text not null default '',
  zone          text,
  dongs         text[] not null default '{}',
  viewed_at     timestamptz not null default now(),
  primary key (user_id, entry_key)
);
create index idx_region_log_recent on public.region_log (user_id, viewed_at desc);

-- 8개 초과분은 자동 정리 (프런트 slice(0, 8)와 같은 역할)
create or replace function public.trim_region_log()
returns trigger language plpgsql security invoker set search_path = public as $$
begin
  delete from public.region_log
  where user_id = new.user_id
    and entry_key not in (
      select entry_key from public.region_log
      where user_id = new.user_id
      order by viewed_at desc limit 8
    );
  return null;
end $$;
create trigger t_trim_region_log after insert or update on public.region_log
  for each row execute function public.trim_region_log();


-- ── 정책 수혜용 인적사항 (민감정보) ─────────────────────────────────────
-- localStorage: beomin_personal_info (PI_DEFAULTS 16개 항목)
-- 원본이 전부 문자열 입력이므로 text로 두고, 필요해지면 컬럼별로 타입을 좁힌다.
create table public.personal_info (
  user_id         uuid primary key references auth.users(id) on delete cascade,
  name            text,
  birth           text,
  gender          text,
  cur_residence   text,
  target_region   text,
  job             text,
  city_years      text,
  farm_career     text,
  land_own        text,
  land_area       text,
  crop            text,
  edu_done        text,
  target_type     text,
  householder     text,
  household_size  text,
  income          text,
  updated_at      timestamptz not null default now()
);
create trigger t_pi_touch before update on public.personal_info
  for each row execute function public.touch_updated_at();
```

### 4.4 챗봇 테이블

```sql
-- 세션 ID는 프런트가 crypto.randomUUID()로 발급하던 값을 그대로 쓴다
-- (CropAdvisor.dc.html componentDidMount 의 this._chatSession).
create table public.chat_sessions (
  id            uuid primary key,
  user_id       uuid references auth.users(id) on delete set null,
  turns         int  not null default 0,          -- SESSION_TURN_LIMIT = 20
  created_at    timestamptz not null default now(),
  last_used_at  timestamptz not null default now()
);

-- 지금은 프런트가 history를 매 요청에 실어 보내지만, 기기 교체·새로고침 후
-- 대화를 이어보려면 서버 쪽 보관이 필요하다(선택 기능).
create table public.chat_messages (
  id            bigserial primary key,
  session_id    uuid not null references public.chat_sessions(id) on delete cascade,
  seq           int  not null,
  role          text not null check (role in ('user','assistant')),
  content       jsonb not null,
  created_at    timestamptz not null default now(),
  unique (session_id, seq)
);

-- data/chat_usage.jsonl 대체 (log_usage() 가 쓰던 필드와 1:1)
create table public.chat_usage (
  id                  bigserial primary key,
  session_id          uuid,
  turn                int,
  input_tokens        int,
  cache_write_tokens  int,
  cache_read_tokens   int,
  output_tokens       int,
  stop_reason         text,
  tools               text[],
  created_at          timestamptz not null default now()
);
create index idx_chat_usage_created on public.chat_usage (created_at desc);
```

### 4.5 캐시 · 사용량 제한 테이블 (서버 함수 전용)

```sql
-- 인메모리 캐시 4종을 한 테이블로 통합. kind로 종류를 구분한다.
create table public.api_cache (
  cache_key   text primary key,        -- 'news:사과' / 'weather:충청북도' / 'weekly:충청북도 충주시 …'
                                       -- 'score:사과|충주시' / 'soil_ec:4511000000'
  kind        text not null check (kind in ('news','weather','weekly','score','soil_ec')),
  payload     jsonb not null,
  expires_at  timestamptz,             -- null = 무기한 (soil_ec 영속 캐시)
  updated_at  timestamptz not null default now()
);
create index idx_api_cache_kind on public.api_cache (kind, expires_at);

-- _session_turns / _ip_day 대체
create table public.rate_limits (
  scope     text not null check (scope in ('ip','session')),
  subject   text not null,             -- IP는 원문 대신 해시 저장 권장 (§8)
  day       date not null default current_date,
  count     int  not null default 0,
  primary key (scope, subject, day)
);
```

**TTL 매핑** (기존 코드 값 유지)

| kind | 기존 상수 | `expires_at` |
|---|---|---|
| `news` | `CACHE_TTL = 1200` | `now() + interval '20 minutes'` |
| `weather` | `WEATHER_TTL = 3*3600` | `now() + interval '3 hours'` |
| `weekly` | `WEEKLY_TTL = 3*3600` / `WEEKLY_TTL_PARTIAL = 300` | 3시간 / `missing`이면 5분 |
| `score` | `CACHE_TTL = 600` | `now() + interval '10 minutes'` |
| `soil_ec` | 디스크 영속 | `null` |

### 4.6 기준 데이터 테이블 (파일 → DB 이관)

함수 번들을 가볍게 만들고 조회를 빠르게 하기 위한 이관 대상.

```sql
create table public.regions (               -- 법정동 코드 (bjd_code.csv / 국토교통부 CSV)
  code        text primary key,
  province    text not null,
  sigungu     text not null default '',
  dong        text not null default '',
  full_name   text not null,
  is_active   boolean not null default true
);
create index idx_regions_names on public.regions (province, sigungu, dong);

create table public.dong_coords (           -- dong_coords.js / sigungu_coordinates.json
  code        text primary key references public.regions(code),
  lat         double precision not null,
  lon         double precision not null
);

create table public.climate_clusters (      -- climate_clustering_final_v3.csv / region_cluster_map.json
  region_key  text primary key,
  cluster_id  int  not null,
  zone        text,
  metrics     jsonb
);

create table public.crop_standards (        -- crop_standards_v2.json
  crop        text not null,
  metric      text not null,                -- pH / OM / Ap / EC / 강수 / 일조 …
  min_value   double precision,
  max_value   double precision,
  optimal     double precision,
  unit        text,
  source      text,
  primary key (crop, metric)
);

create table public.bloom_dates (           -- apple_bloom_dates.csv / pear_bloom_dates.csv
  crop        text not null,
  station_id  text not null,
  year        int  not null,
  bloom_date  date,
  primary key (crop, station_id, year)
);

create table public.hourly_temp_fruit (     -- hourly_temp_fruit_full.csv (11MB) → DB로 옮겨 번들에서 제거
  station_id  text not null,
  observed_at timestamptz not null,
  temp_c      double precision,
  primary key (station_id, observed_at)
);

create table public.policies (              -- policies.json (783KB, {count, policies})
  id          bigserial primary key,
  title       text not null,
  agency      text,
  region      text,
  target      text,
  support     text,
  period      text,
  url         text,
  raw         jsonb not null
);
create index idx_policies_region on public.policies (region);
create index idx_policies_search on public.policies
  using gin (to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(target,'')));
```

> `policies`는 지금 프런트가 `fetch('policies.json')`으로 783KB를 통째로 받는다. DB로 옮기면
> 인적사항 조건에 맞는 정책만 골라 내려줄 수 있어 첫 로딩이 크게 줄어든다.

### 4.7 RLS (Row Level Security)

```sql
-- 사용자 데이터: 본인 행만 읽기/쓰기
alter table public.profiles          enable row level security;
alter table public.my_farm           enable row level security;
alter table public.checklist_status  enable row level security;
alter table public.prep_status       enable row level security;
alter table public.region_log        enable row level security;
alter table public.personal_info     enable row level security;

-- profiles는 PK가 id, 나머지는 user_id
-- `to authenticated`를 붙여 익명 로그인 포함 '로그인한 사용자'로 범위를 좁힌다.
create policy p_profiles_self on public.profiles
  for all to authenticated using (auth.uid() = id) with check (auth.uid() = id);

create policy p_my_farm_self on public.my_farm
  for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy p_checklist_status_self on public.checklist_status
  for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy p_prep_status_self on public.prep_status
  for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy p_region_log_self on public.region_log
  for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy p_personal_info_self on public.personal_info
  for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- 챗봇: 세션은 본인 것만, 메시지는 본인 세션의 것만
alter table public.chat_sessions enable row level security;
alter table public.chat_messages enable row level security;

create policy p_chat_sessions_self on public.chat_sessions
  for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy p_chat_messages_self on public.chat_messages
  for all to authenticated using (exists (
    select 1 from public.chat_sessions s
    where s.id = chat_messages.session_id and s.user_id = auth.uid()
  )) with check (exists (
    select 1 from public.chat_sessions s
    where s.id = chat_messages.session_id and s.user_id = auth.uid()
  ));

-- 서버 전용 테이블: RLS만 켜고 정책을 만들지 않는다 → anon/authenticated 접근 전면 차단,
-- service_role(RLS 우회)만 접근 가능.
alter table public.api_cache   enable row level security;
alter table public.rate_limits enable row level security;
alter table public.chat_usage  enable row level security;

-- 기준 데이터: 누구나 읽기, 쓰기는 service_role만
alter table public.regions           enable row level security;
alter table public.dong_coords       enable row level security;
alter table public.climate_clusters  enable row level security;
alter table public.crop_standards    enable row level security;
alter table public.bloom_dates       enable row level security;
alter table public.hourly_temp_fruit enable row level security;
alter table public.policies          enable row level security;

create policy p_regions_read           on public.regions           for select using (true);
create policy p_dong_coords_read       on public.dong_coords       for select using (true);
create policy p_climate_clusters_read  on public.climate_clusters  for select using (true);
create policy p_crop_standards_read    on public.crop_standards    for select using (true);
create policy p_bloom_dates_read       on public.bloom_dates       for select using (true);
create policy p_hourly_temp_fruit_read on public.hourly_temp_fruit for select using (true);
create policy p_policies_read          on public.policies          for select using (true);

revoke insert, update, delete on public.regions           from anon, authenticated;
revoke insert, update, delete on public.dong_coords       from anon, authenticated;
revoke insert, update, delete on public.climate_clusters  from anon, authenticated;
revoke insert, update, delete on public.crop_standards    from anon, authenticated;
revoke insert, update, delete on public.bloom_dates       from anon, authenticated;
revoke insert, update, delete on public.hourly_temp_fruit from anon, authenticated;
revoke insert, update, delete on public.policies          from anon, authenticated;
```

> 서버 전용 3개 테이블(`api_cache`·`rate_limits`·`chat_usage`)은 Supabase 보안 린터에서
> `rls_enabled_no_policy` **INFO**로 잡힌다. 정책 없이 차단하는 것이 의도이므로 무시해도 되는 항목이다.

### 4.8 RPC (서버 함수가 호출)

```sql
-- 사용량 제한: 조회+증가를 원자적으로. 한도 초과면 -1을 돌려준다.
-- 기존 check_limits(session, ip)를 두 번 호출(ip → session)해서 대체한다.
create or replace function public.bump_rate_limit(
  p_scope text, p_subject text, p_limit int
) returns int language plpgsql security definer set search_path = public as $$
declare cur int;
begin
  insert into public.rate_limits (scope, subject, day, count)
  values (p_scope, p_subject, current_date, 0)
  on conflict (scope, subject, day) do nothing;

  select count into cur from public.rate_limits
   where scope = p_scope and subject = p_subject and day = current_date
   for update;

  if cur >= p_limit then
    return -1;                        -- 초과: IP는 daily_limit, session은 turn_limit
  end if;

  update public.rate_limits set count = count + 1
   where scope = p_scope and subject = p_subject and day = current_date;
  return cur + 1;                     -- 이번이 몇 번째 턴인지
end $$;

-- 캐시 조회: 만료된 항목은 없는 것으로 취급
create or replace function public.cache_get(p_key text)
returns jsonb language sql security definer set search_path = public as $$
  select payload from public.api_cache
   where cache_key = p_key
     and (expires_at is null or expires_at > now());
$$;

-- 캐시 저장 (upsert). p_ttl_seconds가 null이면 무기한(soil_ec).
create or replace function public.cache_put(
  p_key text, p_kind text, p_payload jsonb, p_ttl_seconds int
) returns void language sql security definer set search_path = public as $$
  insert into public.api_cache (cache_key, kind, payload, expires_at, updated_at)
  values (p_key, p_kind, p_payload,
          case when p_ttl_seconds is null then null
               else now() + make_interval(secs => p_ttl_seconds) end,
          now())
  on conflict (cache_key) do update
     set payload = excluded.payload,
         kind = excluded.kind,
         expires_at = excluded.expires_at,
         updated_at = now();
$$;

-- ⚠️ 중요: create function은 EXECUTE 권한을 PUBLIC에 자동 부여한다.
--    anon/authenticated에서만 revoke하면 PUBLIC 상속 경로로 여전히 호출된다
--    (실제로 이 프로젝트 구축 중 보안 린터가 /rest/v1/rpc/cache_put 이 anon에게
--     열려 있다고 잡아냈다 — 캐시 오염이 가능한 상태였다).
--    반드시 PUBLIC에서 회수하고 service_role에만 다시 부여한다.
revoke execute on function public.bump_rate_limit(text, text, int)   from public, anon, authenticated;
revoke execute on function public.cache_get(text)                    from public, anon, authenticated;
revoke execute on function public.cache_put(text, text, jsonb, int)  from public, anon, authenticated;
revoke execute on function public.on_auth_user_created()             from public, anon, authenticated;
revoke execute on function public.touch_updated_at()                 from public, anon, authenticated;
revoke execute on function public.trim_region_log()                  from public, anon, authenticated;

grant execute on function public.bump_rate_limit(text, text, int)   to service_role;
grant execute on function public.cache_get(text)                    to service_role;
grant execute on function public.cache_put(text, text, jsonb, int)  to service_role;

-- 회원가입(익명 로그인 포함) 시 profiles 자동 생성 트리거가 확실히 돌도록
grant execute on function public.on_auth_user_created() to supabase_auth_admin;
```

> 트리거 함수는 트리거 실행 시점에 EXECUTE 권한을 재검사하지 않으므로, PUBLIC에서 회수해도
> `updated_at` 갱신·`region_log` 정리·`profiles` 자동 생성은 정상 동작한다 (실제로 검증했다).

만료 캐시 정리는 Supabase **Cron**(`pg_cron`)으로:

```sql
select cron.schedule('purge-api-cache', '0 * * * *',
  $$ delete from public.api_cache where expires_at is not null and expires_at < now() - interval '1 day' $$);
```

### 4.9 Storage

`Beomin_web/uploads/`가 **20MB**(hero.png 728KB, apple.png 1.5MB, lettuce.png 2.1MB 등). Vercel 정적 배포에 그대로 포함해도 동작하지만, 이미지 교체마다 재배포가 필요하고 저장소가 커진다.

- 버킷 `assets` (public read) 생성 → 이미지 이관 → 프런트 경로를 Storage public URL로 교체
- 대안: 지금처럼 정적 파일로 두고 이관은 나중에 (기능에는 영향 없음)

---

## 5. localStorage → Supabase 마이그레이션

기존 사용자의 기기에 남아 있는 데이터를 잃지 않도록, **최초 1회 업로드**를 넣는다.

```js
// CropAdvisor.dc.html — componentDidMount 안, loadMyFarm() 앞에 배치
const MIGRATED_FLAG = 'beomin_migrated_v1';

async function migrateLocalToSupabase(sb) {
  if (window.localStorage.getItem(MIGRATED_FLAG)) return;

  const { data: { user } } = await sb.auth.getUser();
  if (!user) return;
  const uid = user.id;
  const read = (k) => { try { return JSON.parse(window.localStorage.getItem(k)); } catch (e) { return null; } };

  // 1) 나의 농장
  const mf = read('beomin_my_farm');
  if (mf && mf.crop && mf.region) {
    await sb.from('my_farm').upsert({
      user_id: uid, crop: mf.crop,
      region_code: mf.region.code || '', province: mf.region.province || '',
      sigungu: mf.region.sigungu || '', dong: mf.region.dong || '',
      full_name: mf.region.fullName || '', zone: mf.region.zone || null,
      plan: mf.plan || null, created_at: mf.createdAt || new Date().toISOString()
    });
  }

  // 2) 체크리스트 / 3) 준비할 일  (맵 → 행 배열)
  const cs = read('beomin_checklist_status') || {};
  const csRows = Object.entries(cs).map(([item_key, status]) => ({ user_id: uid, item_key, status }));
  if (csRows.length) await sb.from('checklist_status').upsert(csRows);

  const ps = read('beomin_prep_suggestions') || {};
  const psRows = Object.entries(ps).map(([prep_key, status]) => ({ user_id: uid, prep_key, status }));
  if (psRows.length) await sb.from('prep_status').upsert(psRows);

  // 4) 지역 기록 (배열 순서가 최신순 → viewed_at을 역순으로 만들어 순서 보존)
  const rl = read('beomin_region_log') || [];
  if (rl.length) {
    const base = Date.now();
    await sb.from('region_log').upsert(rl.map((r, i) => ({
      user_id: uid, entry_key: r.key, region_code: r.code || '', dong: r.dong || '',
      name: r.name || '', province: r.province || '', sigungu: r.sigungu || '',
      zone: r.zone || null, dongs: r.dongs || [],
      viewed_at: new Date(base - i * 1000).toISOString()
    })));
  }

  // 5) 인적사항 (camelCase → snake_case)
  const pi = read('beomin_personal_info');
  if (pi) {
    await sb.from('personal_info').upsert({
      user_id: uid, name: pi.name, birth: pi.birth, gender: pi.gender,
      cur_residence: pi.curResidence, target_region: pi.targetRegion, job: pi.job,
      city_years: pi.cityYears, farm_career: pi.farmCareer, land_own: pi.landOwn,
      land_area: pi.landArea, crop: pi.crop, edu_done: pi.eduDone,
      target_type: pi.targetType, householder: pi.householder,
      household_size: pi.householdSize, income: pi.income
    });
  }

  window.localStorage.setItem(MIGRATED_FLAG, new Date().toISOString());
}
```

**동작 원칙**
- localStorage는 **지우지 않는다**. 오프라인·DB 장애 시 폴백으로 계속 유효하다.
- 저장 함수(`applyChecklistStatus`, `applyPrepStatus`, `pushRegionLog`, `setMyCrop`, 인적사항 저장)는
  기존 localStorage 쓰기를 유지한 채 Supabase upsert를 **추가**한다 (write-through).
- 읽기는 로그인 성공 시 DB 우선, 실패하면 localStorage.

---

## 6. Vercel 배포 설계

### 6.1 디렉터리 구조 (전환 후)

```
/
├─ vercel.json
├─ requirements.txt
├─ public/                        ← Beomin_web 정적 파일 이동(또는 outputDirectory 지정)
│   ├─ CropAdvisor.dc.html
│   ├─ RegionMap.html
│   ├─ support.js · image-slot.js · region_tree.js · dong_coords.js
│   └─ uploads/…                  ← Storage 이관 시 삭제 가능
├─ api/
│   ├─ health.py                  ← GET  /api/health
│   ├─ chat.py                    ← POST /api/chat (SSE)
│   ├─ news/[crop].py             ← GET  /api/news/<crop>
│   ├─ weather/[province].py      ← GET  /api/weather/<province>
│   ├─ weekly/[region].py         ← GET  /api/weekly/<region>
│   └─ crop-score/[crop].py       ← GET  /api/crop-score/<crop>?region=
└─ backend/                       ← 기존 모듈 그대로 (함수에서 import)
    ├─ api/ · scoring/ · services/ · utils/
    ├─ chat_schedule.py
    └─ db.py                      ← 신규: Supabase 클라이언트 + cache_get/put·bump_rate_limit 래퍼
```

### 6.2 기존 서버 → 함수 매핑

| 기존 | 신규 | 이식 방법 |
|---|---|---|
| `python -m http.server 8000` | Vercel 정적 호스팅 | 파일 이동만 |
| `news_server.py` `/api/news/<crop>` | `api/news/[crop].py` | `fetch_news()` 재사용, `_cache` → `cache_get/put('news', 1200)` |
| `news_server.py` `/api/weather/<province>` | `api/weather/[province].py` | `fetch_weather()` 재사용, TTL 3h |
| `news_server.py` `/api/weekly/<region>` | `api/weekly/[region].py` | `fetch_weekly()` 재사용, TTL 3h / partial 300s |
| `crop_score_server.py` `/api/crop-score/<crop>` | `api/crop-score/[crop].py` | `build(crop, region)` 재사용, TTL 600s |
| `chat_server.py` `POST /api/chat` | `api/chat.py` | `chat_turn()` 재사용, `check_limits` → `bump_rate_limit`, `log_usage` → `chat_usage` insert |
| `chat_server.py` `GET /api/health` | `api/health.py` | 그대로 |

Vercel Python 런타임은 `BaseHTTPRequestHandler` 기반 `handler` 클래스를 지원하므로, 기존 `do_GET`/`do_POST` 코드를 거의 그대로 옮길 수 있다.

```python
# api/news/[crop].py — 이식 골격
from http.server import BaseHTTPRequestHandler
import json, urllib.parse, sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from backend.db import cache_get, cache_put          # Supabase RPC 래퍼
from backend.news import fetch_news                  # news_server.py에서 분리한 순수 함수

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        crop = urllib.parse.unquote(self.path.rsplit('/', 1)[-1].split('?')[0])
        key = f'news:{crop}'
        data = cache_get(key)
        if data is None:
            data = fetch_news(crop)
            cache_put(key, 'news', data, 1200)       # 기존 CACHE_TTL
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
```

### 6.3 `vercel.json`

```json
{
  "buildCommand": null,
  "outputDirectory": "public",
  "functions": {
    "api/chat.py":              { "maxDuration": 300, "memory": 1024 },
    "api/crop-score/[crop].py": { "maxDuration": 60,  "memory": 1024 },
    "api/weekly/[region].py":   { "maxDuration": 30 },
    "api/weather/[province].py":{ "maxDuration": 30 },
    "api/news/[crop].py":       { "maxDuration": 20 }
  },
  "rewrites": [
    { "source": "/", "destination": "/CropAdvisor.dc.html" }
  ],
  "headers": [
    { "source": "/uploads/(.*)",
      "headers": [{ "key": "Cache-Control", "value": "public, max-age=31536000, immutable" }] }
  ]
}
```

`requirements.txt`

```
anthropic==0.120.0
requests
python-dotenv
supabase
pandas          # 기준 데이터 DB 이관 후 제거 검토 (§6.6)
```

### 6.4 프런트 수정 지점 (정확한 위치)

`Beomin_web/CropAdvisor.dc.html`의 하드코딩된 로컬 주소를 상대경로로 바꾼다. 같은 도메인이 되므로 CORS 헤더도 불필요해진다.

| 줄 | 현재 | 변경 |
|---|---|---|
| 1633 | `const CROP_SCORE_API = 'http://localhost:8002';` | `const CROP_SCORE_API = '';` |
| 1635 | `const CHAT_API = 'http://localhost:8003';` | `const CHAT_API = '';` |
| 3471 | `fetch('http://localhost:8001/api/weekly/' + …)` | `fetch('/api/weekly/' + …)` |
| 3496 | `fetch('http://localhost:8001/api/weather/' + …)` | `fetch('/api/weather/' + …)` |
| 3557 | `fetch('http://localhost:8001/api/news/' + …)` | `fetch('/api/news/' + …)` |
| 3568 | `var api = 'http://localhost:8001';` | `var api = '';` |

추가로 `<head>`에 supabase-js와 익명 로그인 부트스트랩을 넣는다.

```html
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<script>
  // anon 키는 공개용이다. 실제 방어선은 RLS다.
  window.SB = supabase.createClient('https://<PROJECT>.supabase.co', '<ANON_KEY>');
  window.SB_READY = (async () => {
    const { data: { session } } = await window.SB.auth.getSession();
    if (!session) await window.SB.auth.signInAnonymously();
    return window.SB;
  })();
</script>
```

### 6.5 환경변수 (Vercel Project Settings → Environment Variables)

`.env`는 절대 커밋하지 않는다(이미 `.gitignore` 처리됨). 아래 22개를 Vercel에 등록하고, Supabase 3개를 추가한다.

| 변수 | 노출 범위 | 용도 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 서버 전용 | 챗봇 (`claude-opus-5`, MAX_TOKENS 2500) |
| `KMA_SERVICE_KEY` | 서버 전용 | 단기예보 |
| `ASOS_DALY_SERVICE_KEY` / `ASOS_DALY_BASE_URL` / `ASOS_DALY_ENDPOINT` | 서버 전용 | ASOS 일자료 |
| `SOIL_EXAM_LIST_SERVICE_KEY` / `SOIL_EXAM_LIST_BASE_URL` | 서버 전용 | 토양검정 목록 |
| `SOIL_EXAM_STAT_SERVICE_KEY` / `SOIL_EXAM_STAT_BASE_URL` | 서버 전용 | 토양 화학성 통계 |
| `SOIL_EXAM_STAT_OP_PH/OM/AP/KAL/CAL/MG/SA` (7개) | 서버 전용 | 항목별 operation 이름 |
| `NAVER_NEWS_CLIENT_ID` / `NAVER_NEWS_CLIENT_SECRET` / `NAVER_NEWS_ENDPOINT` | 서버 전용 | 뉴스 검색 |
| `NAVER_MAP_CLIENT_ID` | **클라이언트 노출** | 지도 JS SDK — 네이버 콘솔에서 **도메인 제한 필수** |
| `NAVER_MAP_CLIENT_SECRET` | 서버 전용 | (지도 서버 API 사용 시) |
| `BJD_CODE_CSV` | 서버 전용 | 법정동 CSV 경로 → `regions` 테이블 이관 후 불필요 |
| `SUPABASE_URL` | 양쪽 | 프로젝트 URL |
| `SUPABASE_ANON_KEY` | 클라이언트 | RLS 적용 접근 |
| `SUPABASE_SERVICE_ROLE_KEY` | **서버 전용** | 캐시·사용량·로그 (RLS 우회) |

### 6.6 리스크와 대응

| 리스크 | 내용 | 대응 |
|---|---|---|
| 함수 번들 크기 | `pandas` + `hourly_temp_fruit_full.csv`(11MB) + `bjd_code.csv`(3MB)를 함수에 동봉하면 한도(압축 해제 250MB) 압박 | 기준 데이터를 §4.6 테이블로 옮기고 함수는 SQL 조회만. `pandas` 제거 가능해지면 제거 |
| 콜드 스타트 + 느린 공공 API | `crop-score`는 공공 API 여러 개를 직렬 호출 (기존 `SCORE_TIMEOUT = 60`) | `api_cache` 적극 활용, `maxDuration: 60`, 인기 조합은 Cron으로 미리 채우기(워밍) |
| SSE 스트리밍 | 챗봇이 SSE로 토큰을 흘려보냄. 서버리스 스트리밍 동작 확인 필요 | Fluid compute 사용 + `maxDuration: 300`. 문제 시 Node 런타임 프록시로 분리 |
| IP 식별 | `_ip_day`가 쓰던 IP를 서버리스에서는 `x-forwarded-for`에서 읽어야 함 | `request.headers['x-forwarded-for'].split(',')[0]` 사용, 저장은 해시 |
| 대용량 정적/데이터 파일 | `uploads` 20MB + `data/raw` 15MB가 저장소·배포에 부담 | 이미지는 Storage, 데이터는 DB. 저장소에는 원본만 남기고 배포 대상에서 제외 |
| 비용 | Anthropic 토큰 + 공공 API 호출량이 사용자 수에 비례 | `IP_DAILY_LIMIT = 60` / `SESSION_TURN_LIMIT = 20` DB로 강제, `chat_usage`로 모니터링 |

---

## 7. 실행 순서 체크리스트

**1단계 — Supabase 준비**
- [ ] 프로젝트 생성 (리전: Northeast Asia / Seoul)
- [ ] Anonymous sign-ins 활성화
- [ ] §4.2~4.6 DDL 실행 (마이그레이션 파일로 관리)
- [ ] §4.7 RLS 정책 적용 → **anon 키로 남의 행이 안 보이는지 실제 확인**
- [ ] §4.8 RPC + `pg_cron` 정리 작업 등록

**2단계 — 기준 데이터 적재**
- [ ] `regions` ← `data/raw/bjd_code.csv` / 국토교통부 법정동코드 CSV
- [ ] `dong_coords` ← `dong_coords.js` / `sigungu_coordinates.json`
- [ ] `climate_clusters` ← `climate_clustering_final_v3.csv`, `region_cluster_map.json`
- [ ] `crop_standards` ← `crop_standards_v2.json`
- [ ] `bloom_dates` ← `apple/pear_bloom_dates.csv`
- [ ] `hourly_temp_fruit` ← `hourly_temp_fruit_full.csv` (11MB, COPY 사용)
- [ ] `policies` ← `policies.json`
- [ ] 적재 후 기존 스코어링 결과와 **동일한 점수가 나오는지 대조** (`Recommend_top3.csv`, `apple_pear_scores.csv` 기준)

**3단계 — 백엔드 함수화**
- [ ] `news_server.py` / `crop_score_server.py`의 순수 로직을 `backend/` 모듈로 분리 (HTTP 핸들러와 분리)
- [ ] `backend/db.py` 작성 (`cache_get` / `cache_put` / `bump_rate_limit` / `log_usage`)
- [ ] `api/` 6개 함수 작성
- [ ] `soil_ec.py`의 디스크 캐시 → `api_cache('soil_ec', TTL null)`
- [ ] `chat_server.py`의 `log_usage` → `chat_usage` insert
- [ ] `vercel dev`로 로컬 검증

**4단계 — 프런트 전환**
- [ ] §6.4 표의 6개 지점 상대경로화
- [ ] supabase-js + 익명 로그인 부트스트랩 추가
- [ ] 저장 함수 5곳에 write-through upsert 추가
- [ ] `componentDidMount`에 §5 마이그레이션 호출 추가
- [ ] DB 실패 시 localStorage 폴백 동작 확인

**5단계 — 배포**
- [ ] 환경변수 등록 (§6.5)
- [ ] 네이버 지도 콘솔에 배포 도메인 등록 (미등록 시 지도가 안 뜬다)
- [ ] Preview 배포 검증 → Production 승격
- [ ] 검증: 지역 선택 → 적합도 → 작물 선택 → 캘린더 → 체크리스트 → 챗봇 전 구간
- [ ] 다른 브라우저에서 로그인 → 데이터가 따라오는지 확인

---

## 8. 보안 · 개인정보 주의사항

1. **`SUPABASE_SERVICE_ROLE_KEY`는 서버 함수에서만.** 프런트 번들에 들어가면 RLS가 전부 무력화된다.
2. **`ANTHROPIC_API_KEY`는 절대 클라이언트로 내리지 않는다.** 챗봇 호출은 반드시 `/api/chat` 경유.
3. **RLS 없는 테이블을 만들지 않는다.** `api_cache` / `rate_limits` / `chat_usage`는 RLS만 켜고 정책을 두지 않아 `anon` 접근을 차단한다.
4. **`personal_info`는 민감정보다.** 이름·생년·소득·세대 정보가 들어간다.
   - 정책 매칭에 실제로 필요한 항목만 수집
   - 사용자가 직접 삭제할 수 있는 경로 제공 (`delete from personal_info where user_id = auth.uid()`)
   - 보관 기간 정책 명시, 필요 시 Vault/pgsodium으로 컬럼 암호화 검토
5. **IP는 해시로 저장.** `rate_limits.subject`에 원문 IP 대신 `sha256(ip + salt)`를 넣는다.
6. **`NAVER_MAP_CLIENT_ID`는 클라이언트 노출이 불가피**하므로, 네이버 클라우드 콘솔에서 허용 도메인을 배포 도메인으로 제한한다.
7. **`.env` 커밋 금지** — `.gitignore`에 `.env`, `.env.*`(단 `.env.example` 예외)로 이미 처리되어 있다. 키가 필요하면 `.env.example`에 이름만 적는다.
8. 배포 도메인이 정해지면 서버 함수의 `Access-Control-Allow-Origin: *`를 해당 도메인으로 좁힌다 (동일 출처가 되면 헤더 자체가 불필요).

---

## 9. 구축 완료 현황 (2026-08-04)

§4의 스키마를 실제 Supabase 프로젝트에 적용 완료했다.

### 9.1 프로젝트 정보

| 항목 | 값 |
|---|---|
| 프로젝트 이름 | `team_beomin_` |
| 프로젝트 ID (ref) | `xczpkitwsnxpvigyzmud` |
| 리전 | `ap-northeast-2` (서울) |
| Postgres | 17.6.1.155 |
| API URL | `https://xczpkitwsnxpvigyzmud.supabase.co` |
| Publishable key (클라이언트용) | `sb_publishable_JaOeokBlwOoezy1BmKYfng_AZ8MO96J` |
| Service role key | **문서·저장소에 적지 않는다.** 대시보드 → Settings → API Keys에서 복사해 Vercel 환경변수로만 등록 |

> Publishable(=anon) 키는 클라이언트에 노출되도록 설계된 공개 키다. 실제 방어선은 RLS이며, §9.3에서 검증했다.

### 9.2 적용된 마이그레이션 (9개)

| 버전 | 이름 | 내용 |
|---|---|---|
| 20260804053602 | `01_core_and_profiles` | `touch_updated_at`, `profiles`, 회원가입 시 프로필 자동 생성 트리거 |
| 20260804053624 | `02_user_data_tables` | `my_farm`, `checklist_status`, `prep_status`, `region_log`(+8개 유지 트리거), `personal_info` |
| 20260804053638 | `03_chat_tables` | `chat_sessions`, `chat_messages`, `chat_usage` |
| 20260804053653 | `04_cache_and_rate_limits` | `api_cache`, `rate_limits` |
| 20260804053711 | `05_reference_data_tables` | `regions`, `dong_coords`, `climate_clusters`, `crop_standards`, `bloom_dates`, `hourly_temp_fruit`, `policies` |
| 20260804053735 | `06_rls_policies` | 전 테이블 RLS + 정책 15개 |
| 20260804053757 | `07_rpc_functions` | `bump_rate_limit`, `cache_get`, `cache_put` |
| 20260804053813 | `08_cron_purge_api_cache` | `pg_cron` 설치 + 만료 캐시 매시 정리 |
| 20260804054124 | `09_harden_function_privileges` | 함수 `search_path` 고정 + PUBLIC EXECUTE 회수 (§9.4) |

**테이블 18개 전부 RLS 활성화 완료.** `pg_cron` 잡 `purge-api-cache`(`0 * * * *`) 활성 상태.

### 9.3 검증 결과

| 검증 항목 | 방법 | 결과 |
|---|---|---|
| 캐시 왕복 | `cache_put` → `cache_get` | 저장/조회 정상 |
| 캐시 만료 | TTL을 음수로 저장 후 조회 | `null` (만료 항목은 미스 처리) ✔ |
| 영속 캐시 | `soil_ec` TTL `null` | 만료 없이 조회됨 ✔ |
| 사용량 제한 | 한도 3으로 4회 호출 | `1, 2, 3, -1` (4회차 차단) ✔ |
| **RLS 격리** | 사용자 A로 전환 후 조회 | 자기 행 1건만 보임 (B의 인적사항·농장 비노출) ✔ |
| **타인 데이터 수정** | A가 B의 `personal_info` UPDATE 시도 | 0건 (차단) ✔ |
| anon → 서버 전용 테이블 | `anon` 롤로 `api_cache` SELECT | `permission denied` ✔ |
| anon → 남의 사용자 데이터 | `anon` 롤로 `personal_info` SELECT | 0건 ✔ |
| anon → 기준 데이터 | `anon` 롤로 `regions` SELECT | 허용 ✔ |
| 회원가입 트리거 | `auth.users` insert | `profiles` 자동 생성 ✔ (권한 회수 후에도 동작) |
| 보안 린터 | `get_advisors(security)` | WARN 0건 / INFO 3건(의도된 설계) |

검증에 쓴 임시 사용자·캐시·카운터는 모두 삭제했다. 현재 모든 테이블 0행.

### 9.4 구축 중 발견해 고친 문제

1. **RPC가 anon에게 열려 있었다 (실제 취약점).**
   `create function`이 EXECUTE를 PUBLIC에 자동 부여하기 때문에, `anon`/`authenticated`에서만
   revoke한 최초 버전은 무효였다. 로그인 없이 `/rest/v1/rpc/cache_put`으로 캐시 오염이,
   `/rest/v1/rpc/bump_rate_limit`으로 타인의 사용량 소진이 가능한 상태였다.
   → `revoke execute ... from public` + `grant ... to service_role`로 수정 (마이그레이션 09).
2. **트리거 함수의 가변 `search_path`.** `touch_updated_at`, `trim_region_log`에
   `set search_path = public`을 추가해 린터 WARN을 해소했다.
3. **RLS 정책에 `to authenticated` 명시.** 정책 적용 롤을 로그인 사용자로 좁혔다.

### 9.5 남은 작업

- [x] **익명 로그인 활성화** — 대시보드에서 완료(이메일 인증 없이 사이트 이용 가능).
- [ ] 기준 데이터 적재 (§7 2단계) — 테이블은 비어 있다. `hourly_temp_fruit`(11MB)·`regions`(3MB)는
      MCP SQL로 넣기엔 너무 커서 `psql \copy` 또는 Supabase CLI를 쓴다.
- [ ] `SUPABASE_SERVICE_ROLE_KEY`를 대시보드에서 복사해 Vercel 환경변수로 등록
- [ ] `backend/db.py` 작성 후 `cache_get`/`cache_put`/`bump_rate_limit` 연동 (§7 3단계)
- [x] 프런트에 supabase-js 부트스트랩 + write-through upsert + §5 마이그레이션 심기 → **완료 (§11)**

---

## 10. Vercel 배포 완료 현황 (2026-08-04)

### 10.1 배포 정보

| 항목 | 값 |
|---|---|
| 서비스 URL | **https://team-beomin.vercel.app** |
| Vercel 프로젝트 | `21111/team-beomin` (`prj_tENuWjKk9UoQau6qShbmCOAMTxiP`) |
| 배포 방식 | Vercel CLI (`vercel deploy --prod`) — git 연동은 아직 안 함 |
| 정적 루트 | `Beomin_web/` → 빌드 시 `public/`으로 복사 (`.py`·`.bat`은 제거해 소스 노출 방지) |
| 파이썬 런타임 | 3.12 |
| 환경변수 | 26개 등록 (Production + Preview) |

### 10.2 서버 4개 → 서버리스 함수 6개

| 기존 | 배포 경로 | 함수 파일 | 비고 |
|---|---|---|---|
| 8000 정적 | `/` → `/CropAdvisor.dc.html` | (rewrite) | |
| 8001 `/api/news/<작물>` | 동일 | `api/news.py` | CDN 캐시 20분 |
| 8001 `/api/weather/<도>` | 동일 | `api/weather.py` | CDN 캐시 3시간 |
| 8001 `/api/weekly/<지역>` | 동일 | `api/weekly.py` | 3시간 / 부분누락 5분 |
| 8002 `/api/crop-score/<작물>` | 동일 | `api/crop_score.py` | CDN 캐시 10분, maxDuration 60초 |
| 8003 `POST /api/chat` | 동일 | `api/chat.py` | SSE (§10.4) |
| — | `/api/health` | `api/health.py` | 환경변수 존재 여부만 노출 |

경로의 `<작물>` 같은 조각은 `vercel.json`의 `rewrites`로 쿼리스트링으로 바꿔 넘긴다
(`/api/news/사과` → `/api/news?crop=사과`).

### 10.3 배포 검증 결과

| 엔드포인트 | 결과 |
|---|---|
| `/` · `/CropAdvisor.dc.html` | 200 |
| `/api/health` | `env_missing: []` (필수 키 7개 모두 인식) |
| `/api/news/사과` | 200, 실제 기사 6건 |
| `/api/weather/충청북도` | 200 |
| `/api/weekly/충청북도 충주시` | 200 |
| `/api/crop-score/사과?region=충주시` | 200, **68.9점 '양호'**, 6개 지표 전부 산출 (12.8초) |
| `POST /api/chat` | 200, 도구 호출 포함 SSE 정상 — 적합도 68.9점을 근거로 답변 |

### 10.4 로컬과 다른 점 · 알려진 제약

1. **챗봇 스트리밍이 점진적이지 않다.** 로컬(8003)은 SSE 프레임을 직접 chunked로 흘려보내
   토큰이 생기는 대로 표시한다. Vercel 함수 런타임은 전송 인코딩을 자체 처리하므로
   chunk 헤더를 직접 붙이면 응답이 깨진다 → `api/chat.py`는 프레임을 모아 한 번에 내려준다.
   SSE 형식이 같아 프런트 파서는 그대로 동작하지만 답변이 '한 번에' 나타난다.
2. **챗봇 응답이 느리다(약 25초).** 챗봇이 도구로 `/api/crop-score`를 다시 호출하는데
   (`SCORE_API` 환경변수) 그 자체가 12초대다. 함수→함수 호출이라 콜드 스타트가 겹친다.
3. **사용량 제한이 아직 인메모리다.** `chat_server.check_limits`의 `_session_turns`/`_ip_day`는
   함수 인스턴스마다 따로 세므로 실효가 약하다 → §4.8 `bump_rate_limit` RPC로 옮겨야 한다.
4. **DB는 준비됐지만 앱이 아직 쓰지 않는다.** 사용자 데이터는 여전히 localStorage에만 저장된다.
5. `data/cache/ec_cache.json` 쓰기는 읽기 전용 FS에서 실패하지만, `_save_disk_cache()`가
   `OSError`를 잡고 메모리 캐시로 계속 동작한다(무해).

### 10.5 배포 중 발견해 고친 문제

1. **점수가 조용히 0점이 되던 문제 (가장 위험했던 버그).**
   `reading_guard.py`가 읽는 `data/processed/final_weight_matrix.csv`를 함수 번들에
   포함하지 않아 모든 가중치가 `0`이 됐다. 파일이 없으면 예외가 아니라 **weight=0 → 총점 0**이라
   200 응답에 '위험' 등급으로 나왔다(로컬 64.3점 vs 배포 0점). 런타임 데이터 파일 6개를
   전수 조사해 `includeFiles`를 `data/**`로 넓혀 해결 → 68.9점으로 정상화.
2. **토양 지표 3개가 빠지던 문제.** `.vercelignore`에서 `data/raw/bjd_code.csv`를 제외했는데,
   `bjd_lookup.py`가 토양 API용 읍면동 코드를 이 파일에서 읽는다. 제외를 되돌려 해결.
3. **배포 환경에서 네이버 키를 못 읽던 문제.** `news_server.py`의 `load_env()`는 `.env` **파일만**
   본다. 배포 환경엔 파일이 없으므로 `os.environ` 폴백(`_env()`)을 추가했다.
   (`backend/api/*.py`와 `chat_server.py`는 이미 `os.environ`을 쓰고 있어 수정 불필요했다.)
4. **챗봇 모듈 import 실패 가능성.** `chat_server.py`는 모듈 레벨에서 `anthropic.Anthropic()`을
   만들어 키가 없으면 import 자체가 터진다 → `api/chat.py`에서 키 확인 후 지연 import로 바꿨다.
5. **로컬 개발 환경 보존.** 프런트의 API 주소를 상대경로로 통일하면 로컬(8000)에서 API가 전부
   404가 된다. `hostname`으로 두 환경을 자동 구분하도록 했다(`IS_LOCAL`).

### 10.6 다음 단계

- [ ] 네이버 지도 콘솔에 `team-beomin.vercel.app` 도메인 등록 — **안 하면 배포 사이트에서 지도가 안 뜬다**
- [ ] GitHub 저장소를 Vercel에 연결해 push할 때 자동 배포되게 전환 (지금은 CLI 수동 배포)
- [ ] §9.5의 DB 연동 작업(기준 데이터 적재 → `backend/db.py` → 프런트 supabase-js)

---

## 11. 프런트엔드 Supabase 연동 + 로그인/회원가입 (2026-08-04)

`Beomin_web/CropAdvisor.dc.html` 하나에 모두 들어갔다. 이 파일은 `<script type="text/x-dc">`
안의 코드를 support.js 런타임이 **Babel로 변환해** 실행하고, UI는 `{{ }}` / `sc-if` / `sc-for` /
`onClick` 템플릿으로 그린다. 그래서 `async/await`는 쓰지 않고 기존 코드 스타일대로 Promise 체인만 썼다
(regenerator 의존을 피하기 위해).

### 11.0 아이디 로그인 — 실제 이메일은 받지 않는다

사용자는 **아이디 + 비밀번호**로만 가입·로그인한다. 실제 이메일 주소는 수집하지 않고,
인증 메일도 보내지 않는다.

Supabase의 비밀번호 인증은 `email` 필드를 요구하므로, 입력한 아이디를 **내부에서만**
`<아이디>@anong.local` 로 바꿔 쓴다(`idToEmail()` / `emailToId()`). 화면·안내문·오류 메시지
어디에도 이메일이라는 말이 나오지 않으며, 계정 패널에도 아이디만 보인다.

- 아이디 규칙: `^[a-z0-9_]{4,20}$` (영문 소문자·숫자·밑줄 4~20자)
- 입력값은 자동으로 소문자로 모아 저장/조회한다 → 대문자로 적어도 로그인된다
- `anong.local`은 실제로 메일이 닿을 수 없는 도메인이다. 인증 메일 발송 대상 자체가 없다

> ⚠️ **Supabase 설정 전제**: Authentication → Email 프로바이더는 켜둔 채,
> **Confirm email(이메일 인증)은 반드시 꺼져 있어야 한다.** 켜면 `@anong.local`로
> 확인 메일이 발송돼 아무도 가입을 완료할 수 없다. (현재 꺼져 있어 가입 즉시
> `email_confirmed_at`이 채워지고 바로 로그인된다 — 실제 호출로 확인했다.)
> 이 방식의 대가로 **비밀번호 찾기가 불가능하다** — 메일을 보낼 주소가 없기 때문이다.

### 11.1 인증 방식 — 익명 우선, 가입은 선택

```
첫 방문 → signInAnonymously()  →  익명 user_id로 모든 데이터 저장
                                        │
회원가입(같은 세션) → updateUser({email, password})
                                        │  user_id 그대로 유지
                                        ▼
                          지금까지 저장한 데이터가 계정으로 그대로 승계
```

- **로그인을 강제하지 않는다.** 초보 귀농인 대상 서비스에서 가입 강제는 이탈 요인이다.
- 회원가입은 `signUp`이 아니라 **`updateUser`** 로 처리한다 — 익명 사용자에 이메일·비밀번호를
  붙이는 방식이라 `user_id`가 바뀌지 않아 데이터 이전 작업이 아예 필요 없다.
- 로그아웃하면 다시 익명 세션을 발급해 사이트를 계속 쓸 수 있게 한다.
- 로그인으로 사용자가 바뀌면 `clearLocalUserData()`로 이전 사용자의 localStorage·화면 상태를
  먼저 비운 뒤 그 계정의 데이터를 서버에서 받아온다(남의 인적사항이 화면에 남으면 안 된다).

### 11.2 저장 방식 — write-through (localStorage 먼저, 서버에 덧붙여)

| 사용자 동작 | 메서드 | 서버 반영 |
|---|---|---|
| 지도에서 지역 선택 | `pushRegionLog` | `region_log` upsert (8개 상한은 DB 트리거가 정리) |
| 지역 기록 지우기 | `clearRegionLog` | `region_log` 전체 delete |
| 작물 선택(내 농장 확정) | `setMyCrop` | `my_farm` upsert (1인 1행이라 upsert가 곧 교체) |
| 내 농장 해제 | `clearMyFarm` | `my_farm` delete |
| 체크리스트 상태 변경 | `applyChecklistStatus` | 상태 있으면 upsert / '시작 전'이면 delete |
| 예보 준비할 일 상태 변경 | `applyPrepStatus` | 같은 방식 |
| 제안 다시 보기 | `resetPrepDismissed` | `prep_key like '플랜|prep|%' and status='dismissed'` delete |
| 인적사항 입력 | `updatePI` | 0.8초 디바운스 후 `personal_info` 한 행 upsert |
| 인적사항 초기화 | `resetPersonalInfo` | `personal_info` delete (민감정보는 실제로 지운다) |

**localStorage는 계속 유지한다.** 서버 장애·오프라인에서도 앱이 그대로 동작해야 하고,
서버에서 받아온 데이터도 localStorage에 다시 심어 다음 로딩을 빠르게 한다.

불러오기는 `syncFromCloud()`가 5개 테이블을 병렬 조회해 상태로 되돌린다. 서버가 비어 있고
아직 계정이 없는(익명) 사용자면, 기기에 쌓여 있던 기존 데이터를 `migrateLocalToCloud()`로
1회 업로드한다(§5). 마이그레이션 완료 표시는 `beomin_migrated_v1:<user_id>`로 **사용자별**로
남긴다 — 로그인으로 사용자가 바뀌었을 때 이전 사용자의 데이터를 새 계정에 올려버리면 안 된다.

`my_farm`의 `plan`은 서버에도 저장하지만, 불러올 때는 `loadMyFarm()`이 **오늘 기준으로 다시
계산**한다. 농사 계획은 '오늘'을 기준으로 캘린더를 채우므로 저장된 plan을 그대로 쓰면 날짜가 어긋난다.

### 11.3 UI

- 헤더 오른쪽: 로그인 전 `로그인` / 로그인 후 `👤 <아이디>`
- 모달: 로그인·회원가입 탭 전환, 회원가입 탭에는 "지금까지 저장한 내용이 그대로 이 계정으로
  넘어가요" 안내, 로그인 후에는 계정 정보 + 로그아웃
- 엔터로 제출 / ESC로 닫기 — 입력창은 모달을 열고 닫을 때마다 사라졌다 생기므로
  document 위임 리스너(챗봇 입력과 같은 방식)에서 함께 처리한다
- 오류 문구는 Supabase 영문 메시지를 그대로 노출하지 않고 `authMsg()`에서 한국어로 바꾼다
  ("아이디 또는 비밀번호가 맞지 않아요", "이미 쓰고 있는 아이디예요" 등 — '이메일'이라는 말이 새어나가지 않게 한다)

### 11.4 검증 (실제 브라우저 · Playwright + 배포 URL)

| 항목 | 결과 |
|---|---|
| 익명 세션 자동 생성 | OK (`is_anonymous=true`) |
| 익명 상태로 인적사항 저장 | OK |
| 회원가입 후 `user_id` 유지 | **OK (데이터 승계 확인)** |
| 로그아웃 → 새 익명 세션 복귀 | OK |
| 로그아웃 후 이전 사용자 인적사항 노출 | **없음 (빈 칸)** |
| 재로그인 후 데이터 복원 | OK ("복원확인농부" 그대로) |
| localStorage를 비우고 새로고침 | 서버에서 복원됨 (localStorage에도 재기록) |
| `region_log` 10개 저장 | 8개만 유지 (DB 트리거 작동) |
| Supabase / 페이지 오류 | 0건 |

REST 레벨에서도 프런트가 보내는 페이로드 그대로 5개 테이블 저장 → 회원가입 승계 →
새 세션 로그인 조회까지 확인했다. 검증에 쓴 계정 12개는 모두 삭제해 현재 모든 테이블 0행이다.

### 11.5 이번 작업에서 고친 것

1. **`/uploads/chatbot_img.png` 404 (배포 자산 누락).**
   `.vercelignore`에 루트 파일을 지우려고 `chatbot_img.png`라고만 적었는데, `.gitignore`처럼
   **경로 무관 매칭**이라 `Beomin_web/uploads/`의 동명 파일(챗봇 아이콘)까지 배포에서 빠졌다.
   `/chatbot_img.png`로 앵커를 붙여 해결. 페이지가 참조하는 자산 6개 전부 200 확인.
2. **불필요한 민감정보 행 생성.** 화면을 떠날 때 인적사항을 무조건 저장하던 코드가, 폼을
   건드리지 않은 사용자에게도 전부 `null`인 `personal_info` 행을 만들었다 → 저장 안 된 수정이
   있을 때만 보내도록 고쳤다.

### 11.6 남은 것

- [ ] 서버 함수의 캐시·사용량 제한을 DB로 옮기기 (`backend/db.py` + §4.8 RPC) — 지금은 인메모리
- [ ] 기준 데이터 적재 (§7 2단계) — 테이블은 아직 비어 있다
- [ ] **비밀번호 찾기 불가** — 실제 이메일을 받지 않으므로 재설정 메일을 보낼 수 없다.
      필요해지면 (a) 관리자가 `service_role`로 비밀번호를 재설정해주는 창구, 또는
      (b) 가입 시 복구용 질문/코드를 따로 받는 방식 중 하나가 필요하다.
- [ ] 카카오/구글 소셜 로그인 미구현

### 11.7 아이디 로그인 전환 검증 (Playwright + 배포 URL)

| 항목 | 결과 |
|---|---|
| 모달에서 이메일 입력칸·라벨 제거 | **OK (0개)** / 아이디 라벨 1개 |
| 규칙 위반 아이디(`AB`) 거부 | OK — "영문 소문자·숫자·밑줄 4~20자" 안내 |
| 아이디로 회원가입 | OK — 헤더가 `👤 beomin9005977` 로 바뀜 |
| 익명 데이터 승계 (`user_id` 유지) | OK (내부 저장 주소 `beomin9005977@anong.local`) |
| 계정 패널에 내부 도메인 노출 | **없음** ("내 아이디"만 표시) |
| 로그아웃 → 익명 복귀 | OK |
| 아이디로 재로그인 → 데이터 복원 | OK |
| 대문자로 입력해도 로그인 | OK (소문자 정규화) |
| Supabase / 페이지 오류 | 0건 |

### 11.8 "User from sub claim in JWT does not exist" — 세션 자기 회복

**증상**: 회원가입 시 `User from sub claim in JWT does not exist` 오류. 저장도 전부 실패한다.

**원인**: 브라우저 localStorage에 남은 액세스 토큰이 **서버에는 더 이상 없는 사용자**를
가리키는 상태. 계정을 삭제했거나(운영 중 정리 작업 포함) 프로젝트를 초기화한 뒤에 발생한다.
supabase-js는 저장된 세션을 그대로 신뢰하므로, 앱이 스스로 알아채지 못하면 사용자는
가입도 저장도 못 하는 막힌 상태에 빠진다.

**대응** (`CropAdvisor.dc.html`):

1. `initAuth()`가 저장된 세션을 **서버에서 검증**한다 — `getSession()` 뒤에 `getUser()`를
   한 번 호출해(서버까지 가는 호출) 오류면 세션이 죽은 것으로 판단한다.
2. `resetToAnon()`이 로컬 토큰만 버리고(`signOut({ scope: 'local' })`) 새 익명 세션을 만든다.
   서버 로그아웃을 시도하면 없는 사용자라 또 같은 오류가 난다.
3. 회원가입 경로에도 같은 회복을 붙였다 — `updateUser`가 이 오류로 실패하면
   새 익명 세션을 만들고 **한 번 더 시도**한다.
4. 회복까지 실패하면 영문 오류 대신 "로그인 정보가 만료됐어요. 새로고침 후 다시
   시도해주세요."를 보여준다.

**검증**: 익명 세션을 만들고 그 계정을 DB에서 삭제한 뒤, 같은 브라우저 상태로 접속 →
새 익명 세션으로 자동 회복(uid 교체 확인) → 회원가입 성공, 영문 오류 문구 미노출.

> 운영 주의: **익명 계정을 일괄 삭제하지 말 것.** 지금 사이트를 쓰고 있는 사람의 세션을
> 끊어 이 오류를 만든다. 정리해야 한다면 `created_at`이 오래되고 딸린 데이터가 없는
> 익명 계정만 골라 지운다.

---

## 12. heeyeon0804 UI 병합 (2026-08-04)

`heeyeon0804` 브랜치의 UI·기능을 기준으로 삼고, 로그인/Supabase 계층을 그 위에 다시 심었다.

### 12.1 왜 일반 merge를 쓰지 않았나

두 브랜치는 **공통 조상이 없다**(`main` 루트 `de4f7e8` / `heeyeon0804` 루트 `cf29f04`).
`git merge`는 `--allow-unrelated-histories` 없이는 거부하고, 강제하면 양쪽에 다 있는
파일 102개가 전부 충돌로 잡힌다. 그래서 **파일 단위 이식**으로 병합했다.

| 출처 | 가져온 것 |
|---|---|
| heeyeon0804 | `Beomin_web/CropAdvisor.dc.html`, `Beomin_web/RegionMap.html` 전체 |
| main (유지) | 로그인/Supabase 계층, `api/` 함수 6개, `vercel.json`, `requirements.txt`, `.vercelignore`, `Beomin_web/news_server.py` |

`news_server.py`는 heeyeon0804 버전이 `.env` 파일만 읽고 `fetch_weekly`도 없어서
배포 함수(`api/news.py` 등)가 깨진다. `os.environ` 폴백이 있는 main 버전(상위 호환)을 유지했다.

### 12.2 UI 저장 모델이 달라 테이블을 추가했다

heeyeon0804는 **다중 저장 모델**이라 main의 `my_farm`(1인 1행)과 맞지 않는다.
마이그레이션 `10_heeyeon_ui_multi_save_tables`로 3개를 추가했다.

| localStorage 키 | 내용 | 테이블 |
|---|---|---|
| `gwinong_favorites` | 작물 즐겨찾기 (작물명 배열) | **`favorites`** (신규) |
| `beomin_saved_regions` | 저장한 귀농 지역 최대 8곳 | **`saved_regions`** (신규, `raw` jsonb에 원본 항목 보존) |
| `beomin_farm_plans` | 작물별 농사 계획 `{작물: plan}` | **`farm_plans`** (신규) |
| `beomin_personal_info` | 인적사항 16개 항목 | `personal_info` (기존 재사용) |
| `beomin_checklist_status` | 체크리스트 진행 상태 | `checklist_status` (기존 재사용) |

`my_farm` · `prep_status` · `region_log`는 main의 단일 농장 UI 전용이라 이 UI에서는
쓰지 않는다. 데이터가 없으므로 되돌릴 때를 대비해 남겨뒀다.

### 12.3 부수 개선 — 체크리스트가 저장되지 않던 문제

heeyeon0804 UI는 체크리스트 진행 상태를 `setState`만 하고 저장하지 않아 **새로고침하면
사라졌다**. 서버 저장을 붙이는 김에 `applyChecklistStatus()`를 단일 통로로 만들어
localStorage + `checklist_status` 테이블에 함께 남긴다.

### 12.4 검증 (Playwright · 로컬 8000 + 실제 공공 API + 배포)

| 항목 | 결과 |
|---|---|
| 귀농 가이드 탭 렌더 (heeyeon0804 UI 유지) | OK |
| 익명 세션 자동 생성 | OK |
| 익명 상태로 인적사항 저장 | OK |
| 아이디 회원가입 시 `user_id` 승계 | OK (데이터 유지) |
| 로그아웃 → 익명 복귀 | OK |
| 로그아웃 후 이전 사용자 인적사항 노출 | 없음 (빈 칸) |
| 재로그인 후 서버에서 복원 | OK |
| 모달에 이메일 입력칸 | 0개 (아이디 방식) |
| 배포 후 `/api/health` · news · weather · crop-score | 전부 200 |
| Supabase / 페이지 오류 | 0건 |

### 12.5 이 병합으로 사라진 기능 (main에만 있던 것)

heeyeon0804 UI로 교체했으므로 아래는 화면에서 빠졌다. 코드는 `login` 브랜치와
`919b6a1` 커밋에 그대로 남아 있다.

- 챗봇 (`/api/chat` 함수와 `backend/chat_server.py`는 배포에 남아 있으나 UI 진입점이 없다)
- 7일 예보 준비 체크리스트(`prep_status`), 6개 기후 클러스터 코멘트, 지역 방문 기록
- 단일 귀농지역·작물 모델(`my_farm`)
- 품종 UI(`loadCultivars`/`pickCultivar`) — 진행 중이던 별도 작업이라 이식하지 않았다

---

## 13. heeyeon2026 UI 병합 (2026-08-04, §12의 UI를 대체)

`heeyeon2026` 브랜치의 UI·기능을 기준으로 삼고, `login` 브랜치의 Supabase·Vercel
서버 계층을 그 위에 얹었다. 병합 커밋 `0edd7b4`.

이 병합이 §12(heeyeon0804 UI)를 화면에서 대체한다. 단 §12 이후 main에 들어온 품종
추천 백엔드(`37ccf24`)는 그대로 유지한다 — `CropAdvisor.dc.html`을 전혀 건드리지
않는 백엔드·데이터 파일뿐이라 UI 교체와 충돌하지 않는다.

### 13.1 진짜 `git merge`를 쓸 수 있었다

`heeyeon0804`(§12)와 달리 **두 브랜치는 공통 조상이 있다** — `1966000`
("데이터 출처 목록 추가"). 양쪽 모두 그 지점에서 갈라져 나왔다.

```
1966000 ─┬─ e3b9076 → 28ec7bd → 08b79e7 → 919b6a1   (login: Vercel + Supabase)
         └─ ea82024 → 61df050 → 358dce7             (heeyeon2026: 예보 매핑 + UI)
```

그래서 파일 단위 이식이 아니라 `git merge`로 병합했다(`--allow-unrelated-histories`
불필요). 3-way 머지가 대부분을 자동 처리하고 **충돌은 5건**만 났다.

| 충돌 위치 | 해결 |
|---|---|
| API 상수 블록 (`LABELS`/`MEDALS`/주소) | heeyeon2026의 `LABELS`를 살리고, 주소는 login의 `IS_LOCAL` 분기를 채택 |
| `loadWeekly` / `loadWeather` / `openDetail` / `loadNews` 의 `fetch` 4곳 | 하드코딩 `http://127.0.0.1:8001` → `NEWS_API` 상수 |

`MEDALS`·`CROP_PHOTO`는 heeyeon2026이 UI 리디자인으로 참조를 없앤 상수라 되살리지
않았다(병합본 전체에서 참조 0건 확인).

### 13.2 스키마 변경이 필요 없다

heeyeon2026은 **단일 농장 모델**(`my_farm` 1인 1행)을 그대로 유지한다. §4.3의 기존
테이블이 그대로 맞아, heeyeon0804 병합 때 추가했던 `favorites`·`saved_regions`·
`farm_plans`(다중 저장 모델용)는 이번 UI에서는 쓰지 않는다.

| localStorage 키 | 테이블 | 상태 |
|---|---|---|
| `beomin_my_farm` | `my_farm` | 기존 재사용 |
| `beomin_personal_info` | `personal_info` | 기존 재사용 |
| `beomin_checklist_status` | `checklist_status` | 기존 재사용 |
| `beomin_prep_suggestions` | `prep_status` | 기존 재사용 |
| `beomin_region_log` | `region_log` | 기존 재사용 |

### 13.3 heeyeon2026의 백엔드 개선이 배포 경로에도 자동 반영된다

`api/*.py`는 `news_server.fetch_*`를 얇게 감싸는 래퍼다(§6.2). heeyeon2026이 고친
아래 파일들은 그 호출 경로 안쪽에 있어, 함수 코드를 손대지 않아도 배포에 함께 실린다.

- `backend/api/weekly_fcst.py` — 예보 API 병렬화
- `backend/api/midfcst.py` · `midfcst_regions.py` — 특별시/광역시 중기예보 구역 매핑
- `backend/utils/region_mapper.py` — 광역시 구·군 매핑

`vercel.json`의 `includeFiles`가 이미 `backend/**`를 통째로 포함하므로 설정 변경도 없다.

### 13.4 검증

| 항목 | 결과 |
|---|---|
| 병합 충돌 마커 잔존 | 0건 |
| `CropAdvisor.dc.html` JS 문법 (`node --check`) | OK |
| 템플릿 `{{ }}` 바인딩 미해결 | 양쪽 부모와 동일(신규 0건) |
| `state` 필드 / 클래스 메서드 / 최상위 상수 | 양쪽 부모의 합집합 그대로, 유실 0건 |
| heeyeon2026 대비 삭제된 줄 | 7줄 — 전부 하드코딩 API 주소·`state` 한 줄 (의도된 교체) |
| Python 13개 파일 `py_compile` | OK |
| `api/` 함수 6개 import | 전부 OK |
| Supabase 테이블 5개 존재·RLS | 전부 존재, RLS 켜짐 |

### 13.5 알려진 제약 (이번 병합이 만든 것은 아님)

- **품종 추천 UI**(`loadCultivars`/`pickCultivar`, `/api/cultivar-score`)는 아직
  배포 경로에서 동작하지 않는다. 이 병합으로 **서버 구현 자체는 들어왔다**(`37ccf24`의
  `backend/api/cultivar_api.py`·`backend/scoring/cultivar_fit.py`·`data/cultivars/감자.json`).
  그러나 `api/`에 대응 함수가 없고 `vercel.json`에 rewrite도 없어 Vercel에서는
  엔드포인트에 도달할 수 없다. 응답이 없으면 `cultivarBox.show`가 `false`가 되어
  카드 자체가 렌더되지 않으므로 화면에는 드러나지 않는다. 살릴 때는 `api/`
  함수와 `vercel.json` rewrite만 추가하면 된다(백엔드는 이미 있다).
- 로컬 실행에는 `.env`가 필요하다(`.env.example` 복사). 배포는 Vercel 환경변수(§6.5).

---

## 14. origin/main 통합 병합 (2026-08-04)

`merge_login_heeyeon2026`을 main에 올리기 위해 `origin/main`(`37ccf24`)을 브랜치로
머지했다. 브랜치가 `919b6a1`에서 갈라진 뒤 main에 커밋 3개가 더 쌓여 있어
non-fast-forward 상태였다.

### 14.1 main 쪽 커밋을 성격별로 갈랐다

| 커밋 | 건드린 파일 | 처리 |
|---|---|---|
| `d427184` heeyeon0804 UI 병합 | `CropAdvisor.dc.html`, `RegionMap.html` **둘뿐** | **폐기** — §13이 heeyeon2026 UI를 기준으로 삼는다 |
| `2d8b91b` DB.md §12 | `DB.md` | 유지 (§12로 보존) |
| `37ccf24` 품종 추천 백엔드 | `backend/**`, `breed.md`, `data/cultivars/**` | **유지** (자동머지) |

`37ccf24`는 `CropAdvisor.dc.html`을 전혀 건드리지 않는다. 그래서 "heeyeon2026 UI를
쓰면서 품종 백엔드도 갖는다"가 타협 없이 성립한다.

### 14.2 HTML 두 개를 브랜치판으로 되돌렸다 (의도된 결정)

- `CropAdvisor.dc.html` — 충돌 21곳 전부 `d427184`발 heeyeon0804 UI. `--ours` 채택.
  브랜치판이 품종·`cultivar` 참조를 69건 가져 main판(14건)보다 많으므로 잃는 것이 없다.
- `RegionMap.html` — **충돌 없이 자동머지됐지만 결과가 heeyeon0804판이었다.** 브랜치는
  이 파일을 손대지 않았고(merge-base 그대로) main만 바꿨으니 git이 main 쪽을 그대로
  적용한 것이다. 그 결과 heeyeon0804의 "내 귀농지역으로도 저장" 체크박스가 되살아났는데,
  이는 §13.2가 쓰지 않는다고 못박은 다중 저장 모델(`saved_regions`)용이다.
  단일 농장 모델과 어긋나므로 `HEAD` 판으로 되돌렸다.

> 교훈: 자동머지가 조용히 성공한 파일도 확인해야 한다. UI 기준을 한쪽으로 정한
> 병합에서는 "충돌 0건"이 "올바르게 병합됨"을 뜻하지 않는다.

### 14.3 DB.md 충돌

양쪽이 각각 다른 병합을 `## 12.`로 기록해 충돌했다. 어느 쪽도 버릴 수 없으므로
main의 heeyeon0804 기록을 §12로 두고 브랜치의 heeyeon2026 기록을 §13으로 올렸다.
§13.5의 품종 항목은 "서버 구현이 아직 없다"였는데, 이 병합으로 백엔드가 들어왔으므로
"백엔드는 있고 `api/`·`vercel.json` 노출만 없다"로 교정했다.

### 14.4 검증

| 항목 | 결과 |
|---|---|
| 충돌 마커 잔존 | 0건 |
| `CropAdvisor.dc.html` · `RegionMap.html` 이 브랜치판과 동일 | 바이트 일치 |
| 머지로 들어온/변경된 Python 8개 `py_compile` | OK |
| `api/` 서버리스 함수 6개 import | 6/6 OK |
| DB.md 섹션 번호 | §12 → §13 → §14 정상 |

## 15. the_final_frontend 병합 (2026-08-05)

`origin/the_final_frontend`의 프런트엔드 디자인을 받고, 기능은 이 브랜치 것을 유지해
**이 브랜치에 병합**했다.

### 15.1 먼저 잰 것 — 저쪽의 실제 작업은 커밋 2개뿐이다

| 항목 | 값 |
|---|---|
| 분기점 | `28ec7bd` (Supabase 로그인 추가 시점) |
| 내 쪽만 있는 커밋 | **32개** |
| 저쪽만 있는 커밋 | **2개** (`d811470` 챗봇 버튼 크롭·배경 / `39d3c84` 홈 원페이지 스크롤·뉴스 카드) |
| 저쪽 2커밋이 건드린 파일 | `CropAdvisor.dc.html` · `news_server.py` · `hero.png` · `README.md` |

`git diff HEAD origin/the_final_frontend`는 19,382줄 삭제로 보이지만 그건 **내 32개 커밋이
저쪽에 없다**는 뜻이다(품종 시스템·breed.md·data/cultivars 전부). 3-way 병합에서는 삭제되지
않는다 — 저쪽이 지운 것이 아니라 애초에 가진 적이 없기 때문이다.

### 15.2 병합 전에 양쪽 표식을 세어 기준을 만들었다

§12에서 RegionMap.html이 **충돌 0건으로 깨끗하게 자동 병합됐는데 정작 남의 UI로 되돌아간**
일이 있었다. 같은 실수를 막으려고 `merge_audit.py`로 **내 기능 47개 표식**과 **저쪽 디자인
21개 표식**을 병합 전·후로 세었다.

| | 내 기능 빠짐 | 저쪽 디자인 빠짐 |
|---|---|---|
| 병합 전 내 파일 | 0 | 14 |
| 저쪽 파일 | 29 | 0 |
| **병합 후** | **0** | **1** (의도 — 아래) |

남은 1건은 `id="anong-auth-email"`이다. 로그인을 **아이디 방식**으로 바꾼 것이 이 브랜치의
기능 변경이라 이메일 입력은 되살리지 않았다(저쪽 2커밋도 로그인을 건드리지 않았다).

### 15.3 충돌 17건의 판정 기준 — "theirs"가 곧 저쪽 디자인은 아니다

충돌의 '저쪽' 쪽에는 두 가지가 섞여 있다. **저쪽이 새로 쓴 것**과 **분기점 그대로인 것**이다.
후자를 받으면 내가 교체한 것이 조용히 되돌아간다. 그래서 판정은 git 충돌 표시가 아니라
**저쪽 2커밋의 실제 패치**와 대조해서 했다.

- **theirs (디자인)** — 챗봇 버튼 74px 원형 크롭·`object-fit:contain`, `activeTab:'landing'`,
  작물 카드 버튼 라벨
- **ours (기능)** — 아이디 로그인 3건, `chatbot_img.png?v=2`(vercel이 uploads를 1년
  immutable로 서빙해 파일명을 그대로 두면 캐시가 안 깨진다), **작물 결과 카드 2건**
- **both (합침)** — `markJustSignedUp`, `buildPlanFor` 계열 4건, myFarm 필드

작물 결과 카드가 그 함정이었다. 충돌의 '저쪽' 쪽이 `rankStyle`·`photo`·`medal`을 쓰는데,
이 필드들은 **분기점에 이미 있던 것**이고 저쪽 2커밋은 건드리지 않았다. 그대로 받았으면
내가 만든 카드가 사라지고, 내가 지운 `CROP_PHOTO`를 참조해 **빈 카드**가 됐을 것이다.

### 15.4 일정 보정 두 가지를 합쳤다 — 순서가 중요하다

두 브랜치가 같은 `buildPlanFor`에서 서로 다른 일정 보정을 한다.

- 내 것 `cvAdjust` — 품종의 파종일·생육일수(과수는 수확기)로 단계를 **재배치**
- 저쪽 `startInfo` — 사용자가 "지금 이 작업을 하고 있어요"라고 고른 단계가 오늘 오도록
  모든 사이클을 **통째로 이동**

`buildPlanFor(cropName, region, cvAdjust, cultivationType, startInfo)`로 합치고 **품종 보정 →
시작 시점 보정** 순으로 적용한다. 품종이 일정의 '모양'을 만들고, 시작 시점이 그 모양을
사용자의 실제 진행에 맞춘다. 거꾸로 하면 품종 보정이 사용자가 맞춘 위치를 흐트러뜨린다.

합치면서 저쪽 코드의 결함 두 개를 고쳤다.
1. 기준 단계를 `computeSeasonInstance(season, today)`(**보정 전** 일정)에서 찾고 있었다.
   품종 보정을 적용한 뒤 일정에서 찾아야 오프셋이 어긋나지 않는다.
2. 단계를 `{task, start, end}`로만 다시 만들어 **`note`·`period`가 사라졌다**. 체크리스트가
   그 값을 쓰므로 `{...s}`로 모든 필드를 살린다.

저쪽 '지금 상태' 입력(Q1 시작 시점 / Q2 생육 단계)은 `hasFarmPlan` 안, 농업일지 제목 앞에
두었다. 품종을 골라야 단계 목록이 확정되고 그래야 Q2를 고를 수 있기 때문이다.

### 15.5 화면에서만 드러난 결함 2건

정적 검사(표식·태그 짝·참조 무결성)를 모두 통과한 뒤에도 브라우저에서 두 개가 나왔다.

1. **랜딩 4번째 카드가 "undefined 소식"** — state 초기화 줄
   (`justSignedUp·heroDismissed·newsRotateIdx·homeIdx`)이 통째로 빠져 있었다.
   `newsRotateIdx`가 undefined → `NEWS_ROTATE_CROPS[NaN]`. `homeIdx`가 없으면 원페이지 휠
   스크롤도 첫 화면에서 동작하지 않는다.
2. **그림카드에 "undefined 품종별 날짜 자료가 없어…"** — `updateFarmStartInfo`와
   `setMyCrop`이 **옛 3인자 시그니처**로 `buildPlanFor`를 불러 `startInfo`가 `cvAdjust`
   자리에 들어갔다. 호출부 6곳을 모두 새 시그니처로 맞췄다.

### 15.6 검증

| 항목 | 결과 |
|---|---|
| 충돌 표시 잔여 | 0건 |
| `sc-if` / `sc-for` 짝 | 126/126 · 40/40 |
| 끊어진 참조(`CROP_PHOTO`·`authEmailVal` 등) | 0건 |
| 내 기능 표식 | 47/47 |
| 저쪽 디자인 표식 | 20/21 (아이디 로그인으로 의도 제외 1건) |
| 브라우저 — 랜딩 | 히어로·게스트/로그인·카드 4개 정상 |
| 브라우저 — 프로필 | 작형 카드·추천 근거 토글·작형별 품종 선택·품종 게이트 정상 |
| 브라우저 — 품종 선택 후 | 상추(작형) · 감자(품종) · 사과(수확기) 3작물 모두 일지 생성 + Q1/Q2 표시 |
| 두 보정 합성 | 품종 보정 문구 유지된 채 Q2 변경 시 일정 이동 확인 |
| JS 오류 | 0건 |
